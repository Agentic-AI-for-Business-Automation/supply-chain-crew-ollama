"""Run an n8n Code-node file under Node with mocked $input / $('Node') so its logic can be unit-tested."""
import json, os, shutil, subprocess
import pytest

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
NODE = shutil.which("node") or shutil.which("nodejs")
RUNNER = r"""
const fs = require('fs');
const body = fs.readFileSync(process.argv[1], 'utf8');
const ctx = JSON.parse(fs.readFileSync(0, 'utf8'));
const mk = j => ({ json: j });
const $input = { first: () => mk(ctx.input[0]), all: () => ctx.input.map(mk) };
const $ = name => {
  if (!(name in ctx.refs)) throw new Error('Node ' + name + ' has not been executed');
  const a = ctx.refs[name];
  return { first: () => mk(a[0]), all: () => a.map(mk) };
};
const fn = new Function('$input', '$', body);
process.stdout.write(JSON.stringify(fn($input, $)));
"""


def run_node(js_file: str, input_items: list, refs: dict | None = None) -> list:
    """Returns the list of output items' json."""
    if not NODE:
        pytest.skip("node is not installed")
    r = subprocess.run([NODE, "-e", RUNNER, os.path.join(ROOT, "n8n", "js", js_file)],
                       input=json.dumps({"input": input_items, "refs": refs or {}}), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-500:]
    return [item["json"] for item in json.loads(r.stdout)]
