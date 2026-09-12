"""Entry point for the packaged backend server (used by PyInstaller)."""
import argparse
import uvicorn
from app.main import app


def main():
    parser = argparse.ArgumentParser(description="OHM Backend Server")
    parser.add_argument("--port", type=int, default=4243)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    # Published so POST /api/shutdown can ask this server to exit on demand.
    # uvicorn.run() would keep the Server instance to itself, leaving no way to
    # stop it from inside a request handler.
    app.state.uvicorn_server = server
    server.run()


if __name__ == "__main__":
    main()
