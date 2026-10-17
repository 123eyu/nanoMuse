"""The issue forms stay on the *Create new issue* chooser.

A form's top-level ``description`` is the line the chooser shows beside its name, and GitHub
rejects a form whose description is outside 3..200 characters *without a message* — the form
parses fine and simply never appears. That is how #495 lost the general bug form: appending
the Chinese half took it to 245 characters, and desktop, runtime and web reports were left
with no form at all while the Android form's own text still pointed at the missing one.

The forms are read as plain lines, the way ``test_release_docs.py`` treats them, so no YAML
dependency is needed to catch it here before GitHub does.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"

# What the chooser must offer: the general form and the two narrower ones. config.yml is the
# chooser's own contact links, not a form.
REQUIRED_FORMS = ("app_bug_report.yml", "bug_report.yml", "feature_request.yml")


def _header(path: Path) -> dict[str, str]:
    """The unindented ``key: value`` lines of the form, up to and including ``body:``."""
    header: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("body:"):
            header["body"] = ""
            break
        if not line or line[0].isspace():  # a nested key or a body list item
            continue
        key, sep, value = line.partition(":")
        if sep:
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            header[key] = value
    return header


def _forms() -> list[tuple[Path, dict[str, str]]]:
    """Every ``.yml`` in the template directory that is an issue form, not the chooser config."""
    found = [(path, _header(path)) for path in sorted(TEMPLATES.glob("*.yml"))]
    return [(path, header) for path, header in found if "body" in header]


def test_the_three_forms_are_on_disk() -> None:
    names = {path.name for path, _ in _forms()}
    assert set(REQUIRED_FORMS) <= names, f"missing from the chooser: {set(REQUIRED_FORMS) - names}"


def test_every_form_carries_a_name_and_a_description() -> None:
    for path, header in _forms():
        for key in ("name", "description"):
            assert header.get(key), f"{path.name} has no top-level {key}, so the form cannot open"


def test_a_description_fits_the_chooser() -> None:
    # GitHub's own limit for the form schema; outside it the form is dropped silently (#495).
    for path, header in _forms():
        description = header["description"]
        assert 3 <= len(description) <= 200, (
            f"{path.name}'s description is {len(description)} characters; GitHub keeps the form "
            "off the new-issue chooser unless it is 3-200"
        )
