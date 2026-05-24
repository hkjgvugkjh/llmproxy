"""CLI entry point for llmproxy."""

import sys


def main():
    """Run the LLM Proxy server."""
    import argparse
    import uvicorn
    from .server import create_app

    parser = argparse.ArgumentParser(description="LLM Proxy server")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument("--host", default=None, help="Override bind host")
    parser.add_argument("--port", type=int, default=None, help="Override bind port")
    args = parser.parse_args()

    app = create_app(config_path=args.config)

    # Read host/port from config if not overridden
    import configparser
    import os
    config_path = args.config or os.environ.get("LLMPROXY_CONFIG", "config.ini")
    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    host = args.host or cfg.get("proxy", "host", fallback="0.0.0.0")
    port = args.port or cfg.getint("proxy", "port", fallback=8000)

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
