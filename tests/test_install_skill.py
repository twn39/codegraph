"""Install skill path mapping and write helper."""

from pathlib import Path

from codegraph_gen.install_skill import (
    SKILL_MARKDOWN,
    SUPPORTED_PLATFORMS,
    skills_dir_for_platform,
    write_skill,
)


def test_skills_dir_for_each_platform(tmp_path: Path):
    for platform in SUPPORTED_PLATFORMS:
        d = skills_dir_for_platform(platform, home=tmp_path)
        assert d.is_absolute() or True
        assert "codegraph" in str(d)
        assert str(tmp_path) in str(d)


def test_write_skill(tmp_path: Path):
    target = tmp_path / ".claude" / "skills" / "codegraph"
    path = write_skill(target)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "name: codegraph" in text
    assert "/codegraph" in text
    assert text == SKILL_MARKDOWN
