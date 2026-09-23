#!/usr/bin/env python3
# =============================================================================
# register_mlflow.py -- register the PS2 cascade scorer in MLflow for lineage,
# consistent with PS1/PS3/PS5. The model is a pyfunc wrapping the SAME shared
# ps2_scoring_core (params-only), routed by a 'family' column, so the
# registered model is also runnable standalone -- while the Batch Transform
# serves the BYOC ECR image.
#
# Uses the notebook's EXISTING MLflow experiment (chicago-ps2-cascade-analysis)
# so this run's lineage sits alongside the analysis-only runs already logged
# there (markov/hmm/recurrence cells) -- no new experiment needed.
#
# Usage:
#   python register_mlflow.py --outputs ps2_model_out \
#     --tracking-uri http://cubic-mars-mlflow-server-dev:5000 \
#     --name cubic-mars-ps2-cascade --image-uri 170202974600.dkr.ecr...:v1.0
# =============================================================================
import argparse, glob, json, os


class PS2ScorerModel:
    """mlflow.pyfunc.PythonModel: loads markov/hmm/recurrence params, routes by 'family' column."""
    def load_context(self, context):
        import glob as _g, json as _j
        self.markov, self.hmm, self.recurrence = {}, None, None
        for p in _g.glob(os.path.join(context.artifacts["params"], "*_markov_params.json")):
            d = _j.load(open(p)); self.markov[str(d.get("category", "")).upper()] = d
        hp = os.path.join(context.artifacts["params"], "ps2_hmm_params.json")
        if os.path.exists(hp):
            self.hmm = _j.load(open(hp))
        rp = os.path.join(context.artifacts["params"], "ps2_recurrence_params.json")
        if os.path.exists(rp):
            self.recurrence = _j.load(open(rp))
        import ps2_scoring_core as core
        self._core = core

    def predict(self, context, model_input):
        import pandas as pd
        df = model_input
        family = str(df["family"].iloc[0]).lower() if "family" in df.columns else None
        if family == "markov":
            out = []
            for cat, g in df.groupby(df["mars_device_category"].astype(str).str.upper()):
                p = self.markov.get(str(cat).upper())
                if p:
                    out.append(self._core.score("markov", p, g))
            return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
        if family == "hmm" and self.hmm:
            return self._core.score("hmm", self.hmm, df)
        if family == "recurrence" and self.recurrence:
            return self._core.score("recurrence", self.recurrence, df)
        return pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="ps2_model_out")
    ap.add_argument("--params-dir", default=None)
    ap.add_argument("--core", default="../container/ps2_scoring_core.py")
    ap.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI"))
    ap.add_argument("--experiment", default="chicago-ps2-cascade-analysis")
    ap.add_argument("--name", default="cubic-mars-ps2-cascade")
    ap.add_argument("--image-uri", default="")
    args = ap.parse_args()

    import shutil, tempfile
    import mlflow

    pdir = args.params_dir or tempfile.mkdtemp(prefix="ps2params_")
    if not args.params_dir:
        for p in (glob.glob(os.path.join(args.outputs, "*_markov_params.json"))
                  + glob.glob(os.path.join(args.outputs, "ps2_hmm_params.json"))
                  + glob.glob(os.path.join(args.outputs, "ps2_recurrence_params.json"))):
            shutil.copy(p, pdir)

    edv = "2026-07-24.v1"
    n_markov_cats = len(glob.glob(os.path.join(pdir, "*_markov_params.json")))

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(args.experiment)
    with mlflow.start_run(run_name=f"ps2-cascade-scorer-{edv}") as run:
        mlflow.set_tags({"event_def_version": edv, "event_definition": "cascade_chain",
                         "image_uri": args.image_uri, "serving": "sagemaker-batch-transform (BYOC)",
                         "grain": "device+serial", "families": "markov,hmm,recurrence"})
        mlflow.log_param("n_markov_categories", n_markov_cats)
        info = mlflow.pyfunc.log_model(
            artifact_path="model", python_model=PS2ScorerModel(),
            artifacts={"params": pdir}, code_paths=[args.core],
            registered_model_name=args.name,
            pip_requirements=["numpy<2", "pandas>=2.0", "pyarrow>=14"],
        )
        print(f"[mlflow] logged + registered {args.name} | event={edv} | run={run.info.run_id}")
        print(f"MLFLOW_MODEL_URI={info.model_uri}")


if __name__ == "__main__":
    main()
