"""Check repository-local Markdown link and image destinations."""

import posixpath
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


def heading_anchors(tokens: list[Token]) -> set[str]:
    counts: Counter[str] = Counter()
    anchors = set()
    for opening, heading in zip(tokens, tokens[1:], strict=False):
        if opening.type != "heading_open":
            continue
        title = "".join(child.content for child in heading.children or [])
        slug = re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")
        anchor = slug
        while anchor in anchors:
            counts[slug] += 1
            anchor = f"{slug}-{counts[slug]}"
        anchors.add(anchor)
    return anchors


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    files = {PurePosixPath(name) for name in tracked if name}
    directories = {parent for path in files for parent in path.parents}
    parser = MarkdownIt().enable("table")
    documents = {
        path: parser.parse(Path(path).read_text(encoding="utf-8"))
        for path in sorted(files)
        if path.suffix.lower() == ".md"
    }
    anchors = {path: heading_anchors(tokens) for path, tokens in documents.items()}
    errors = []
    for path, tokens in documents.items():
        for token in tokens:
            for child in token.children or []:
                attribute = {"link_open": "href", "image": "src"}.get(child.type)
                destination = child.attrGet(attribute) if attribute else None
                if not destination:
                    continue
                link = urlsplit(destination)
                if link.scheme or link.netloc:
                    continue
                local_path = unquote(link.path)
                if local_path.startswith("/"):
                    target = PurePosixPath(posixpath.normpath(local_path.lstrip("/")))
                elif local_path:
                    target = PurePosixPath(posixpath.normpath(path.parent / local_path))
                else:
                    target = path
                if target not in files and target not in directories:
                    problem = "missing local path"
                elif (
                    link.fragment
                    and target in anchors
                    and unquote(link.fragment) not in anchors[target]
                ):
                    problem = "missing Markdown heading"
                else:
                    continue
                line = token.map[0] + 1 if token.map else 1
                errors.append(f"{path}:{line}: {problem}: {destination}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Local links and images checked in {len(documents)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
