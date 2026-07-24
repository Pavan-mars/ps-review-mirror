#!/usr/bin/env python3
# =============================================================================
# register_mlflow.py -- register the PS5 RUL scorer in MLflow for lineage,
# consistent with PS1/PS3. The model is a pyfunc wrapping the SAME shared
# ps5_scoring_core (params-only), so the registered model is also runnable
# standalone -- while the Batch Transform serves the BYOC ECR image.
#
# Tags every version with event_def_version, per-type cv_cindex, gate_pass, and
# the ECR image_uri, so any RDS row traces back to (model version, image, event).
#
# Usage:
#   python register_mlflow.py --outputs PS5_reliability_v5_outputs \
#     --tracking-uri http://cubic-mars-mlflow-server-dev:5000 \
#     --name cubic-mars-ps5-rul --image-uri 170202974600.dkr.ecr...:v5.1
# =============================================================================
import argparse, glob, json, os


class PS5ScorerModel:
    """mlflow.pyfunc.PythonModel: loads the slim params, scores device/serial frames."""
    def load_context(self, context):
        import json as _j
        self.dev, self.ser = {}, {}
        for p in glob.glob(os.path.join(context.artifacts["params"], "*_device_survival_params.json")):
            d = _j.load(open(p)); self.dev[str(d.get("category", "")).upper()] = d
        for p in glob.glob(os.path.join(context.artifacts["params"], "*_serial_params.json")):
            d = _j.load(open(p)); self.ser[str(d.get("category", "")).upper()] = d
        import ps5_scoring_core as core
        self._core = core

    def predict(self, context, model_input):
        import pandas as pd
        df = model_input
        is_serial = "COMPONENT_SERIAL_NBR" in df.columns
        table = self.ser if is_serial else self.dev
        fn = self._core.score_serials if is_serial else self._core.score_devices
        out = []
        if "mars_device_category" in df.columns:
            for cat, g in df.groupby(df["mars_device_category"].astype(str).str.upper()):
                if str(cat).upper() in table:
                    out.append(fn(table[str(cat).upper()], g))
        elif len(table) == 1:
            out.append(fn(next(iter(table.values())), df))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="PS5_reliability_v5_outputs")
    ap.add_argument("--params-dir", default=None, help="dir with the 6 param JSONs (default: gather from --outputs)")
    ap.add_argument("--core", default="../container/ps5_scoring_core.py", help="path to ps5_scoring_core.py")
    ap.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI"))
    ap.add_argument("--name", default="cubic-mars-ps5-rul")
    ap.add_argument("--image-uri", default="")
    args = ap.parse_args()

    import shutil, tempfile
    import mlflow
    import pandas as pd  # noqa: F401

    # gather params into one dir (the pyfunc artifact)
    pdir = args.params_dir or tempfile.mkdtemp(prefix="ps5params_")
    if not args.params_dir:
        for p in (glob.glob(os.path.join(args.outputs, "*/*_device_survival_params.json"))
                  + glob.glob(os.path.join(args.outputs, "*/*_serial_params.json"))):
            shutil.copy(p, pdir)

    # collect provenance from the device params
    summ = {}
    for p in glob.glob(os.path.join(pdir, "*_device_survival_params.json")):
        d = json.load(open(p)); summ[d["category"]] = {"cv_cindex": d.get("cv_cindex"), "gate_pass": d.get("gate_pass")}
    edv = next((json.load(open(p)).get("event_def_version")
                for p in glob.glob(os.path.join(pdir, "*_device_survival_params.json"))), "2026-07-23.v1")

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("cubic-mars-ps5-reliability")
    with mlflow.start_run(run_name=f"ps5-rul-{edv}") as run:
        mlflow.set_tags({"event_def_version": edv, "event_definition": "hw_oos_set",
                         "image_uri": args.image_uri, "serving": "sagemaker-batch-transform (BYOC)",
                         "grain": "device+serial"})
        for cat, m in summ.items():
            if m.get("cv_cindex") is not None:
                mlflow.log_metric(f"cv_cindex_{cat.lower()}", float(m["cv_cindex"]))
            mlflow.set_tag(f"gate_pass_{cat.lower()}", bool(m.get("gate_pass")))
        info = mlflow.pyfunc.log_model(
            artifact_path="model", python_model=PS5ScorerModel(),
            artifacts={"params": pdir}, code_paths=[args.core],
            registered_model_name=args.name,
            pip_requirements=["numpy<2", "pandas>=2.0", "pyarrow>=14"],
        )
        print(f"[mlflow] logged + registered {args.name} | event={edv} | run={run.info.run_id}")
        print(f"MLFLOW_MODEL_URI={info.model_uri}")


if __name__ == "__main__":
    main()
