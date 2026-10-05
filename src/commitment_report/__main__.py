"""启动气候承诺进度汇编服务。"""

from __future__ import annotations

import argparse

from .api import serve
from .service import CommitmentService


def main() -> None:
    parser = argparse.ArgumentParser(description="气候承诺进度汇编服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = serve(CommitmentService(), args.host, args.port)
    print(f"listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
