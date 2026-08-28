from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELP_TEXT = """----- common params -----

-h,    --help, --usage                  print usage and exit
       --version                        show version and build info
-t,    --threads N                      number of threads to use during generation
                                        (env: LLAMA_ARG_THREADS)
-tb,   --threads-batch N                number of threads to use during batch and prompt processing
-c,    --ctx-size N                     size of the prompt context (default: 4096)
                                        (env: LLAMA_ARG_CTX_SIZE)
-n,    --predict, --n-predict N         number of tokens to predict (default: -1)
-b,    --batch-size N                   logical maximum batch size (default: 2048)
-ngl,  --gpu-layers, --n-gpu-layers N   number of layers to store in VRAM
-m,    --model FNAME                    model path (default: `models/7B/ggml-model-f16.gguf`)
-a,    --alias STRING                   set alias for model name
       --flash-attn                     enable Flash Attention (default: disabled)
       --mlock                          force system to keep model in RAM
       --temp N                         temperature (default: 0.8)
       --top-k N                        top-k sampling (default: 40)
       --rope-freq-base N               RoPE base frequency
       --cache-type-k TYPE              KV cache data type for K (default: f16)
       --numa TYPE                      attempt optimizations that help on some NUMA systems
       --flavour-of-the-month STRING    an option the curated layer has never heard of

----- server params -----

       --host HOST                      ip address to listen (default: 127.0.0.1)
       --port PORT                      port to listen (default: 8080)
       --no-webui                       disable the web ui (default: enabled)
       --embeddings                     restrict to only support embedding use case
"""

VERSION_TEXT = "version: 9999 (abcdef1)\nbuilt with stub for tests"
DEVICES_TEXT = "Available devices:\n  CPU: stub processor (0 MiB, 0 MiB free)"
GPU_DEVICES_TEXT = "Available devices:\n  CUDA0: stub accelerator (24576 MiB, 24000 MiB free)"


def flag_value(argv: list[str], flag: str, fallback: str) -> str:
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(f"{flag}="):
            return item.split("=", 1)[1]
    return fallback


def build_handler(
    ready_at: float, generate: bool, plain: bool = False
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                if time.monotonic() < ready_at:
                    self.reply(503, {"status": "loading model"})
                    return
                self.reply(200, {"status": "ok"})
                return
            if self.path.startswith("/props"):
                self.reply(200, {"default_generation_settings": {"n_ctx": 4096}})
                return
            self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) or b"{}"
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            if not generate:
                self.reply(500, {"error": {"message": "stub refuses to generate"}})
                return
            messages = payload.get("messages") or []
            last = (
                "plain answer with no marker at all"
                if plain
                else (messages[-1].get("content", "") if messages else "")
            )
            self.reply(
                200,
                {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": f"stub reply: {last}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 3},
                },
            )

        def reply(self, code: int, body: dict[str, object]) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(HELP_TEXT)
        return 0
    if "--version" in argv:
        print(VERSION_TEXT, file=sys.stderr)
        return 0
    if "--list-devices" in argv:
        print(GPU_DEVICES_TEXT if "--stub-gpu" in argv else DEVICES_TEXT)
        return 0
    print(f"stub: command line {' '.join(argv)}", flush=True)
    if "--stub-fail" in argv:
        print("stub: error: unknown argument, refusing to start", flush=True)
        return 1
    port = int(flag_value(argv, "--port", "8080"))
    host = flag_value(argv, "--host", "127.0.0.1")
    ready_at = time.monotonic() + float(flag_value(argv, "--stub-delay", "0"))
    server = ThreadingHTTPServer(
        (host, port),
        build_handler(ready_at, "--stub-mute" not in argv, "--stub-plain" in argv),
    )
    print(f"stub: server is listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
