from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Iterator

from tree_sitter import Language, Node, Parser, Tree
import tree_sitter_c


def _build_parser() -> Parser:
    parser = Parser()
    parser.language = Language(tree_sitter_c.language())
    return parser


@dataclass(slots=True)
class ParsedCFile:
    path: Path
    text: str
    tree: Tree

    @classmethod
    def from_path(cls, path: Path) -> "ParsedCFile":
        text = path.read_text(encoding="utf-8")
        parser = _build_parser()
        tree = parser.parse(text.encode("utf-8"))
        return cls(path=path, text=text, tree=tree)

    @property
    def root(self) -> Node:
        return self.tree.root_node

    def text_for(self, node: Node) -> str:
        return self.text[node.start_byte : node.end_byte]

    def replace_node_text(self, node: Node, replacement: str) -> str:
        return self.text[: node.start_byte] + replacement + self.text[node.end_byte :]

    def line_end_after(self, index: int) -> int:
        if index < len(self.text) and self.text[index] == "\n":
            return index + 1
        line_break = self.text.find("\n", index)
        if line_break < 0:
            return len(self.text)
        return line_break + 1


def iter_nodes(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from iter_nodes(child)


def find_first_node(parsed: ParsedCFile, *, kind: str, predicate: Callable[[Node], bool] | None = None) -> Node:
    for node in iter_nodes(parsed.root):
        if node.type != kind:
            continue
        if predicate is None or predicate(node):
            return node
    raise ValueError(f"Unable to find node kind={kind!r} in {parsed.path}.")


def find_include_node(parsed: ParsedCFile, include_text: str) -> Node:
    return find_first_node(
        parsed,
        kind="preproc_include",
        predicate=lambda node: include_text in parsed.text_for(node),
    )


def find_define_node(parsed: ParsedCFile, define_name: str) -> Node:
    return find_first_node(
        parsed,
        kind="preproc_def",
        predicate=lambda node: re.search(rf"#define\s+{re.escape(define_name)}\b", parsed.text_for(node)) is not None,
    )


def find_function_definition(parsed: ParsedCFile, function_name: str) -> Node:
    def _matches(node: Node) -> bool:
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            return False
        return re.search(rf"\b{re.escape(function_name)}\s*\(", parsed.text_for(declarator)) is not None

    return find_first_node(parsed, kind="function_definition", predicate=_matches)


def find_field_declaration(parsed: ParsedCFile, field_name: str) -> Node:
    return find_first_node(
        parsed,
        kind="field_declaration",
        predicate=lambda node: re.search(rf"\b{re.escape(field_name)}\b", parsed.text_for(node)) is not None,
    )


def find_for_statement(parsed: ParsedCFile, *, function_name: str, needle: str) -> Node:
    function_node = find_function_definition(parsed, function_name)
    for node in iter_nodes(function_node):
        if node.type == "for_statement" and needle in parsed.text_for(node):
            return node
    raise ValueError(f"Unable to find target for_statement in {function_name} within {parsed.path}.")


def find_if_statement(parsed: ParsedCFile, *, function_name: str, needle: str) -> Node:
    function_node = find_function_definition(parsed, function_name)
    for node in iter_nodes(function_node):
        if node.type == "if_statement" and needle in parsed.text_for(node):
            return node
    raise ValueError(f"Unable to find target if_statement in {function_name} within {parsed.path}.")
