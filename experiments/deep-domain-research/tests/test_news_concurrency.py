import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class ConcurrentNewsWritesTest(unittest.TestCase):
    def test_concurrent_identical_writers_replay_one_complete_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = """
import json, sys, time
from pathlib import Path
from command_io import CommandError
from news_storage import store_result
root, number = Path(sys.argv[1]), sys.argv[2]
value = {"payload": "x" * 16000000}
(root / ("ready-" + number)).touch()
while not (root / "start").exists():
    time.sleep(0.005)
try:
    result = store_result(value, {"project_id":"p", "run_id":"r"}, root, "value")
    print(json.dumps(result))
except CommandError as exc:
    print(json.dumps(exc.error))
    raise SystemExit(1)
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(root), str(index)],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(6)
            ]
            try:
                deadline = time.monotonic() + 10
                while len(list(root.glob("ready-*"))) != len(processes):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                (root / "start").touch()
                references = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                    references.append(json.loads(stdout))
                self.assertTrue(
                    all(reference == references[0] for reference in references)
                )
                saved = json.loads((root / references[0]["path"]).read_bytes())
                self.assertEqual(len(saved["payload"]), 16000000)
                self.assertEqual(
                    list((root / "p" / "r").iterdir()), [root / "p/r/value.json"]
                )
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
