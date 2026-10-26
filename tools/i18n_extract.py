#!/usr/bin/env python3
"""Wrap user-visible Spanish strings in tr() and collect the catalog.

    i18n_extract.py --dry FILE...      list candidates
    i18n_extract.py --apply FILE...    rewrite files in place, print catalog keys as JSON

f-strings become tr("text {0}").format(expr) so translators see placeholders.
"""
import ast
import json
import re
import sys

SPANISH = re.compile(r"[áéíóúñÁÉÍÓÚÑ¿¡]|\b(de|la|el|los|las|que|en|con|por|para|una|un|no|se|sin|al|del|es|y|o|tu|tus|su|lo|le|si|más|ya|hay|está|son|mi|mis)\b", re.I)
UI_CALLS = {"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "Section", "setText", "setToolTip",
            "setWindowTitle", "addTab", "addRow", "setPlaceholderText", "addAction", "addMenu", "A", "_notify",
            "showMessage", "warning", "critical", "information", "question", "setHeaderLabels", "setInformativeText",
            "addButton", "setSuffix", "setSpecialValueText", "getText", "getOpenFileName", "getSaveFileName",
            "append", "step", "HelperError", "setStatusTip", "setTitle", "combo", "spin", "QAction", "QMenu"}
# log patterns, command arguments and config values that must stay as they are
EXCLUDE = {"no shared key found", "Autorización denegada", "--cache=no", "--no-pager", '  oifname "lo" accept',
           "no", "Del", "SwanGUI", "User-Agent", "Preshared Key", "KiB", "MiB", "GiB", "TiB"}
REGEXY = re.compile(r"\\[dswSb(]|\(\?|\.\*|\[\^|\|\w")


def ui_context(parents):
    for p in reversed(parents):
        if isinstance(p, ast.Call):
            f = p.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            return name in UI_CALLS
    return False


def is_text(s, parents):
    if s in EXCLUDE or s.startswith("Ctrl+"):
        return False
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}", s) or REGEXY.search(s):
        return False
    if s.strip() != s.strip().strip("_-./") or re.fullmatch(r"[\w./:-]+", s) and not ui_context(parents):
        # identifiers, paths, keys
        if not SPANISH.search(s):
            return False
    if SPANISH.search(s):
        return True
    return ui_context(parents) and re.match(r"[A-ZÁÉÍÓÚ¿¡]", s) is not None


class Finder(ast.NodeVisitor):
    def __init__(self, src):
        self.src = src
        self.parents = []
        self.found = []  # (node, kind, key)
        self.skip = set()

    def generic_visit(self, node):
        self.parents.append(node)
        super().generic_visit(node)
        self.parents.pop()

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return  # docstring / bare string
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if getattr(f, "id", None) == "tr":
            return  # already wrapped
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name in ("compile", "search", "match", "sub", "fullmatch", "findall", "split", "run", "Popen",
                    "_run", "run_swanctl", "lookup", "get", "pop", "setdefault", "startswith", "endswith",
                    "find", "findtext", "SubElement", "sub", "replace", "strftime", "strptime", "setObjectName",
                    "setProgram", "setArguments", "getenv", "join", "open", "fullmatch", "index"):
            if name not in ("join",):
                for a in node.args:
                    self.skip.add(id(a))
        self.generic_visit(node)

    def visit_Compare(self, node):
        for n in [node.left, *node.comparators]:
            self.skip.add(id(n))
            for sub in ast.walk(n):
                self.skip.add(id(sub))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        self.skip.add(id(node.slice))
        self.generic_visit(node)

    def visit_Dict(self, node):
        for k in node.keys:
            if k is not None:
                self.skip.add(id(k))
        self.generic_visit(node)

    def visit_JoinedStr(self, node):
        if id(node) in self.skip:
            return
        text, args = [], []
        for v in node.values:
            if isinstance(v, ast.Constant):
                text.append(v.value.replace("{", "{{").replace("}", "}}"))
            else:
                spec = ""
                if v.conversion != -1:
                    spec += "!" + chr(v.conversion)
                if v.format_spec is not None:
                    if any(not isinstance(x, ast.Constant) for x in v.format_spec.values):
                        return
                    spec += ":" + "".join(x.value for x in v.format_spec.values)
                text.append("{" + str(len(args)) + spec + "}")
                args.append(ast.get_source_segment(self.src, v.value))
        key = "".join(text)
        plain = re.sub(r"\{\d+[^}]*\}", "", key)
        if args and None not in args and is_text(plain, self.parents):
            self.found.append((node, "f", key, args))

    def visit_Constant(self, node):
        if id(node) in self.skip or not isinstance(node.value, str):
            return
        if isinstance(self.parents[-1], ast.JoinedStr):
            return
        if is_text(node.value, self.parents):
            self.found.append((node, "s", node.value, None))


def offsets(src):
    starts, pos = [0], 0
    for line in src.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    return starts


def process(path, apply):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fd = Finder(src)
    fd.visit(tree)
    keys = []
    if not apply:
        for node, kind, key, _ in fd.found:
            print(f"{path}:{node.lineno}: {key!r}")
        return [k for _, _, k, _ in fd.found]
    starts = offsets(src)
    edits = []
    for node, kind, key, args in fd.found:
        a = starts[node.lineno - 1] + len(src[starts[node.lineno - 1]:].encode()[:node.col_offset].decode())
        b = starts[node.end_lineno - 1] + len(src[starts[node.end_lineno - 1]:].encode()[:node.end_col_offset].decode())
        lit = json.dumps(key, ensure_ascii=False)
        new = f"tr({lit})" if kind == "s" else f"tr({lit}).format({', '.join(args)})"
        edits.append((a, b, new))
        keys.append(key)
    for a, b, new in sorted(edits, reverse=True):
        src = src[:a] + new + src[b:]
    ast.parse(src)
    open(path, "w", encoding="utf-8").write(src)
    return keys


if __name__ == "__main__":
    apply = sys.argv[1] == "--apply"
    allkeys = []
    for f in sys.argv[2:]:
        allkeys += process(f, apply)
    if apply:
        print(json.dumps(sorted(set(allkeys)), ensure_ascii=False, indent=0))
