# @file: backend/nlp/cli.py
# @exports: main
# @depends_on: backend/nlp/worker.py
# @purpose: Native one-shot or continuous Redis NLP worker command.
import argparse
import asyncio

from backend.nlp.worker import run_worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true')
    asyncio.run(run_worker(parser.parse_args().watch))


if __name__ == '__main__':
    main()
