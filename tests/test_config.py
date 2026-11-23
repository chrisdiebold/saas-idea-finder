import shutil

import pytest
import yaml

from ideafinder.config import ConfigError, load_config


def test_repo_config_loads(repo_config_dir):
    config = load_config(repo_config_dir)
    assert [s.key for s in config.sources.enabled()] == [
        "hackernews",
        "jira_atlassian",
        "wordpress",
        "stackexchange",
        "github",
        "shopify_apps",
        "feature_boards",
        "zapier",
        "producthunt",
        "mastodon",
        "bluesky",
    ]
    assert config.scoring.publish.min_authors == 5
    assert config.models.tasks["briefing"].provider == "manual"


@pytest.fixture
def config_copy(tmp_path, repo_config_dir):
    for name in ("sources.yaml", "scoring.yaml", "models.yaml"):
        shutil.copy(repo_config_dir / name, tmp_path / name)
    return tmp_path


def _edit(path, change):
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))


def test_weights_must_sum_to_one(config_copy):
    _edit(config_copy / "scoring.yaml", lambda d: d["weights"].update(volume=0.9))
    with pytest.raises(ConfigError, match=r"(?s)scoring.yaml.*sum to 1.0"):
        load_config(config_copy)


def test_every_task_needs_a_route(config_copy):
    _edit(config_copy / "models.yaml", lambda d: d["tasks"].pop("summary"))
    with pytest.raises(ConfigError, match="no route for tasks.*summary"):
        load_config(config_copy)


def test_tasks_must_use_configured_providers(config_copy):
    _edit(config_copy / "models.yaml", lambda d: d["providers"].pop("lmstudio"))
    with pytest.raises(ConfigError, match="providers that aren't configured"):
        load_config(config_copy)


def test_duplicate_source_keys_rejected(config_copy):
    _edit(config_copy / "sources.yaml", lambda d: d["sources"].append(dict(d["sources"][0])))
    with pytest.raises(ConfigError, match="duplicate source keys"):
        load_config(config_copy)


def test_unknown_fields_rejected(config_copy):
    _edit(config_copy / "sources.yaml", lambda d: d["sources"][0].update(typo_field=True))
    with pytest.raises(ConfigError, match="typo_field"):
        load_config(config_copy)


def test_missing_file_named_in_error(config_copy):
    (config_copy / "models.yaml").unlink()
    with pytest.raises(ConfigError, match="models.yaml: file not found"):
        load_config(config_copy)
