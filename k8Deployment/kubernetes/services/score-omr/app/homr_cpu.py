"""Run homr with ONNX thread pools sized for this two-CPU container.

homr creates several independent sessions. ONNX's host-core defaults and
idle spinning can oversubscribe the cgroup limit severely. Apply options
before importing homr so segmentation, transformer and OCR share this rule.
See https://onnxruntime.ai/docs/performance/tune-performance/threading.html.
"""

import cv2
import onnxruntime as ort


class CpuInferenceSession(ort.InferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, **kwargs):
        options = sess_options if sess_options is not None else ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        super().__init__(path_or_bytes, sess_options=options, **kwargs)


def main():
    cv2.setNumThreads(1)
    ort.InferenceSession = CpuInferenceSession
    from homr.main import main as homr_main

    homr_main()


if __name__ == "__main__":
    main()
