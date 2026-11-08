"""リポジトリの YAML がすべて読めること（queria の push は壊れた .table.yml で止まる）。"""

import subprocess

import pytest
import yaml

FILES = subprocess.run(
    ["git", "ls-files", "*.yml", "*.yaml"], capture_output=True, text=True, check=True
).stdout.split()


@pytest.mark.parametrize("path", FILES)
def test_yaml_loads(path):
    with open(path, encoding="utf-8") as f:
        yaml.safe_load(f)
