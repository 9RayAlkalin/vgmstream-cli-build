from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import subprocess

from .c_tree import (
    ParsedCFile,
    find_define_node,
    find_for_statement,
    find_function_definition,
    find_if_statement,
)


TARGET_FILES = (
    Path("cli/CMakeLists.txt"),
    Path("cli/vgmstream_cli.c"),
    Path("cli/vgmstream_cli.h"),
    Path("cli/vgmstream_cli_utils.c"),
    Path("cli/virace_cli_ext.h"),
    Path("src/CMakeLists.txt"),
)

DEFAULT_AUDIT_PATCH = Path("audit/cli-overlay.audit.patch")
OVERLAY_HEADER = Path("overlay/cli/virace_cli_ext.h")


class InjectionError(RuntimeError):
    """Raised when the injector cannot safely update the upstream source tree."""


@dataclass(slots=True)
class InjectorPaths:
    workspace_root: Path
    repo_root: Path
    audit_output: Path

    @classmethod
    def resolve(
        cls,
        *,
        workspace_root: Path,
        repo_root: Path,
        audit_output: Path | None = None,
    ) -> "InjectorPaths":
        workspace_root = workspace_root.resolve()
        repo_root = repo_root.resolve()
        audit_output = (audit_output or (workspace_root / DEFAULT_AUDIT_PATCH)).resolve()
        return cls(workspace_root=workspace_root, repo_root=repo_root, audit_output=audit_output)


def normalize_newlines(text: str, newline: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sanitize_diff_line(line: str) -> str:
    if line == " ":
        return ""
    if line == "+ ":
        return "+"
    if line == "- ":
        return "-"
    if line.startswith(("---", "+++", "@@")):
        return line
    if line and line[0] in {" ", "+", "-"}:
        return line[0] + line[1:].rstrip()
    return line.rstrip()


def replace_once(text: str, old: str, new: str, description: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise InjectionError(f"Expected exactly one match for {description}, found {count}.")
    return text.replace(old, new, 1)


def insert_after_line(text: str, line_fragment: str, block: str, description: str) -> str:
    if block in text:
        return text
    anchor = text.find(line_fragment)
    if anchor < 0:
        raise InjectionError(f"Unable to locate line for {description}.")
    line_end = text.find("\n", anchor)
    if line_end < 0:
        line_end = len(text)
    else:
        line_end += 1
    return text[:line_end] + block + text[line_end:]


def upsert_marked_block(text: str, block: str, start_marker: str, end_marker: str, *, fallback_anchor: str, description: str, insert_before: bool = False) -> str:
    start = text.find(start_marker)
    if start >= 0:
        end = text.find(end_marker, start)
        if end < 0:
            raise InjectionError(f"Found start marker but not end marker for {description}.")
        end += len(end_marker)
        return text[:start] + block + text[end:]

    anchor = text.find(fallback_anchor)
    if anchor < 0:
        fallback_anchor = fallback_anchor.strip()
        anchor = text.find(fallback_anchor)
    if anchor < 0:
        raise InjectionError(f"Unable to find insertion anchor for {description}.")

    if insert_before:
        insert_at = anchor
    else:
        insert_at = anchor + len(fallback_anchor)

    return text[:insert_at] + block + text[insert_at:]


def replace_node_text(path: Path, node_getter, replacement_text: str) -> None:
    parsed = ParsedCFile.from_path(path)
    node = node_getter(parsed)
    new_text = parsed.replace_node_text(node, replacement_text)
    write_text(path, new_text)


def insert_after_include(path: Path, include_text: str, new_include_line: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = insert_after_line(text, include_text, new_include_line, f"include {include_text}")
    write_text(path, updated)


def insert_after_field_line(path: Path, field_line: str, new_field_line: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = insert_after_line(text, field_line, new_field_line, f"field line {field_line}")
    write_text(path, updated)


def replace_function_region_once(path: Path, function_name: str, old: str, new: str, description: str) -> None:
    parsed = ParsedCFile.from_path(path)
    node = find_function_definition(parsed, function_name)
    region = parsed.text_for(node)
    region = replace_once(region, old, new, description)
    write_text(path, parsed.replace_node_text(node, region))


def update_function_region(path: Path, function_name: str, updater) -> None:
    parsed = ParsedCFile.from_path(path)
    node = find_function_definition(parsed, function_name)
    region = parsed.text_for(node)
    updated_region = updater(region)
    if updated_region != region:
        write_text(path, parsed.replace_node_text(node, updated_region))


def insert_before_node(path: Path, node_getter, block: str, marker_begin: str | None = None, marker_end: str | None = None) -> None:
    parsed = ParsedCFile.from_path(path)
    if marker_begin and marker_end and marker_begin in parsed.text:
        updated = parsed.text
        start = updated.index(marker_begin)
        end = updated.index(marker_end, start) + len(marker_end)
        updated = updated[:start] + block + updated[end:]
        search_from = start + len(block)
        while marker_begin in updated[search_from:]:
            dup_start = updated.index(marker_begin, search_from)
            dup_end = updated.index(marker_end, dup_start) + len(marker_end)
            updated = updated[:dup_start] + updated[dup_end:]
        write_text(path, updated)
        return
    if block in parsed.text:
        return
    node = node_getter(parsed)
    updated = parsed.text[: node.start_byte] + block + parsed.text[node.start_byte :]
    write_text(path, updated)


def replace_for_loop(path: Path, function_name: str, needle: str, new_loop: str) -> None:
    parsed = ParsedCFile.from_path(path)
    if "/* VIRACE_EXT_INPUT_LOOP_BEGIN */" in parsed.text:
        start = parsed.text.index("/* VIRACE_EXT_INPUT_LOOP_BEGIN */")
        end = parsed.text.index("/* VIRACE_EXT_INPUT_LOOP_END */", start) + len("/* VIRACE_EXT_INPUT_LOOP_END */")
        updated = parsed.text[:start] + new_loop + parsed.text[end:]
        write_text(path, updated)
        return

    node = find_for_statement(parsed, function_name=function_name, needle=needle)
    updated = parsed.replace_node_text(node, new_loop)
    write_text(path, updated)


def insert_before_if_statement(path: Path, function_name: str, needle: str, block: str) -> None:
    parsed = ParsedCFile.from_path(path)
    if "/* VIRACE_EXT_FINISH_SUMMARY_BEGIN */" in parsed.text:
        start = parsed.text.index("/* VIRACE_EXT_FINISH_SUMMARY_BEGIN */")
        end = parsed.text.index("/* VIRACE_EXT_FINISH_SUMMARY_END */", start) + len("/* VIRACE_EXT_FINISH_SUMMARY_END */")
        updated = parsed.text[:start] + block + parsed.text[end:]
        write_text(path, updated)
        return

    node = find_if_statement(parsed, function_name=function_name, needle=needle)
    updated = parsed.text[: node.start_byte] + block + parsed.text[node.start_byte :]
    write_text(path, updated)


def copy_overlay_header(paths: InjectorPaths) -> None:
    source = paths.workspace_root / OVERLAY_HEADER
    target = paths.repo_root / "cli/virace_cli_ext.h"
    if not source.is_file():
        raise InjectionError(f"Overlay header not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def apply_vgmstream_cli_cmakelists(paths: InjectorPaths) -> None:
    path = paths.repo_root / "cli/CMakeLists.txt"
    newline = detect_newline(path.read_text(encoding="utf-8"))
    block = normalize_newlines("\t\ttarget_link_options(vgmstream_cli PRIVATE /ENTRY:wmainCRTStartup)\n", newline)
    updated = insert_after_line(
        path.read_text(encoding="utf-8"),
        "if(MSVC)",
        block,
        "Windows CMake unicode entrypoint",
    )
    write_text(path, updated)


def apply_libvgmstream_cmakelists(paths: InjectorPaths) -> None:
    path = paths.repo_root / "src/CMakeLists.txt"
    newline = detect_newline(path.read_text(encoding="utf-8"))
    block = normalize_newlines(
        "if(WIN32)\n"
        "\ttarget_compile_definitions(libvgmstream PRIVATE VGM_STDIO_UNICODE)\n"
        "endif()\n",
        newline,
    )
    updated = insert_after_line(
        path.read_text(encoding="utf-8"),
        "setup_target(libvgmstream)",
        block,
        "libvgmstream Windows unicode stdio",
    )
    write_text(path, updated)


def apply_vgmstream_cli_h(paths: InjectorPaths) -> None:
    path = paths.repo_root / "cli/vgmstream_cli.h"
    newline = detect_newline(path.read_text(encoding="utf-8"))
    insert_after_field_line(path, "    bool validate_extensions;", normalize_newlines("    bool delete_source;\n", newline))


def apply_vgmstream_cli_utils(paths: InjectorPaths) -> None:
    path = paths.repo_root / "cli/vgmstream_cli_utils.c"
    newline = detect_newline(path.read_text(encoding="utf-8"))

    insert_after_include(path, '#include "vgmstream_cli.h"', normalize_newlines('#include "virace_cli_ext.h"\n', newline))
    replace_function_region_once(
        path,
        "replace_filename",
        normalize_newlines("    char stream_name[CLI_PATH_LIMIT];\n", newline),
        normalize_newlines("    char stream_name[CLI_PATH_LIMIT];\n    char basename[CLI_PATH_LIMIT];\n    char path[CLI_PATH_LIMIT];\n", newline),
        "replace_filename scratch variables",
    )
    def _inject_wildcards(region: str) -> str:
        return upsert_marked_block(
            region,
            normalize_newlines(
                """        /* VIRACE_EXT_WILDCARDS_BEGIN */
        else if (pos[1] == 'b') {
            virace_extract_basename_without_extension(cfg->infilename, basename, sizeof(basename));

            pos[0] = '%';
            pos[1] = 's'; /* use %s */
            snprintf(tmp, sizeof(tmp), buf, basename);
        }
        else if (pos[1] == 'p') {
            virace_extract_parent_path(cfg->infilename, path, sizeof(path));

            pos[0] = '%';
            pos[1] = 's'; /* use %s */
            snprintf(tmp, sizeof(tmp), buf, path);
        }
        /* VIRACE_EXT_WILDCARDS_END */
""",
                newline,
            ),
            "/* VIRACE_EXT_WILDCARDS_BEGIN */",
            "/* VIRACE_EXT_WILDCARDS_END */",
            fallback_anchor=normalize_newlines("        else if (pos[1] == 's') {\n", newline),
            description="replace_filename wildcard hooks",
            insert_before=True,
        )

    update_function_region(path, "replace_filename", _inject_wildcards)


def apply_vgmstream_cli_c(paths: InjectorPaths) -> None:
    path = paths.repo_root / "cli/vgmstream_cli.c"
    newline = detect_newline(path.read_text(encoding="utf-8"))

    insert_after_include(path, '#include "vgmstream_cli.h"', normalize_newlines('#include "virace_cli_ext.h"\n', newline))

    parsed = ParsedCFile.from_path(path)
    app_name_node = find_define_node(parsed, "APP_NAME")
    replacement = normalize_newlines('#define CUSTOM_BUILD_TAG " (mod by Virace)"\n#define APP_NAME  "vgmstream CLI decoder " VGMSTREAM_VERSION CUSTOM_BUILD_TAG\n', newline)
    if "CUSTOM_BUILD_TAG" not in parsed.text:
        write_text(path, parsed.replace_node_text(app_name_node, replacement))

    replace_function_region_once(
        path,
        "print_usage",
        normalize_newlines('            "       <outfile> wildcards can be ?s=subsong, ?n=stream name, ?f=infile\\n"\n', newline),
        normalize_newlines('            "       <outfile> wildcards can be ?s=subsong, ?n=stream name, ?f=infile, ?b=basename, ?p=path\\n"\n', newline),
        "print_usage wildcard help",
    )
    replace_function_region_once(
        path,
        "print_usage",
        normalize_newlines('            "    -I: print requested file info as JSON\\n"\n', newline),
        normalize_newlines('            "    -I: print requested file info as JSON\\n"\n            "    -Y: delete source file after successful conversion\\n"\n', newline),
        "print_usage -Y help",
    )
    replace_function_region_once(
        path,
        "parse_config",
        normalize_newlines('    while ((opt = getopt(argc, argv, "+o:l:f:d:ipPcmxeLEFrgb2:s:tTk:K:hOvD:S:B:VIwW:")) != -1) {\n', newline),
        normalize_newlines('    while ((opt = getopt(argc, argv, "+o:l:f:d:ipPcmxeLEFrgb2:s:tTk:K:hOvD:S:B:VIwW:Y")) != -1) {\n', newline),
        "parse_config getopt string",
    )
    replace_function_region_once(
        path,
        "parse_config",
        normalize_newlines(
            """            case '2':
                cfg->stereo_track = atoi(optarg) + 1;
                break;
""",
            newline,
        ),
        normalize_newlines(
            """            case '2':
                cfg->stereo_track = atoi(optarg) + 1;
                break;
            /* VIRACE_EXT_OPTION_Y_BEGIN */
            case 'Y':
                cfg->delete_source = true;
                break;
            /* VIRACE_EXT_OPTION_Y_END */
""",
            newline,
        ),
        "parse_config case Y hook",
    )
    insert_before_node(
        path,
        lambda parsed_file: find_function_definition(parsed_file, "main"),
        normalize_newlines(
            """/* VIRACE_EXT_CONVERT_INPUT_FILE_BEGIN */
static bool convert_input_file(cli_config_t* cfg, const char* infilename) {
    bool res;

    cfg->infilename = infilename;
    if (cfg->outfilename_config)
        cfg->outfilename = NULL;

    if (cfg->subsong_index > 0 && cfg->subsong_end != 0) {
        res = convert_subsongs(cfg);
    }
    else {
        cfg->subsong_current_index = cfg->subsong_index;
        res = convert_file(cfg);
    }

    if (res && cfg->delete_source) {
        if (virace_remove_path(cfg->infilename) == 0) {
            printf("source file deleted: %s\\n", cfg->infilename);
        }
        else {
            fprintf(stderr, "could not delete source file: %s\\n", cfg->infilename);
        }
    }

    return res;
}
/* VIRACE_EXT_CONVERT_INPUT_FILE_END */

""",
            newline,
        ),
        marker_begin="/* VIRACE_EXT_CONVERT_INPUT_FILE_BEGIN */",
        marker_end="/* VIRACE_EXT_CONVERT_INPUT_FILE_END */",
    )
    replace_function_region_once(
        path,
        "main",
        normalize_newlines("    bool res, ok;\n", newline),
        normalize_newlines("    bool res, ok;\n    int processed_file_count = 0;\n", newline),
        "main processed_file_count",
    )
    replace_for_loop(
        path,
        "main",
        "for (int i = 1; i < argc; i++)",
        normalize_newlines(
            """/* VIRACE_EXT_INPUT_LOOP_BEGIN */
for (int i = 1; i < argc; i++) {
    // ignore flags
    if (i < CLI_MAX_FLAGS && cfg.flag_index[i]) {
        continue;
    }

    if (virace_is_directory(argv[i])) {
        virace_file_list_t file_list = {0};

            if (!virace_collect_directory_files(argv[i], &file_list) || file_list.count <= 0) {
            fprintf(stderr, "no .wem files found in directory: %s\\n", argv[i]);
            virace_free_input_file_list(&file_list);
            continue;
        }

        for (int j = 0; j < file_list.count; j++) {
            processed_file_count++;
            res = convert_input_file(&cfg, file_list.items[j]);
            if (res) ok = true;
        }

        virace_free_input_file_list(&file_list);
        continue;
    }

    processed_file_count++;
    res = convert_input_file(&cfg, argv[i]);
    //if (!res) goto fail;
    if (res) ok = true;
}
/* VIRACE_EXT_INPUT_LOOP_END */
""",
            newline,
        ),
    )
    insert_before_if_statement(
        path,
        "main",
        "if (!ok)",
        normalize_newlines(
            """/* VIRACE_EXT_FINISH_SUMMARY_BEGIN */
    if (processed_file_count > 0) {
        printf("\\nFinished processing %d file(s).\\n", processed_file_count);
    }
/* VIRACE_EXT_FINISH_SUMMARY_END */

""",
            newline,
        ),
    )


def apply_overlay(paths: InjectorPaths) -> None:
    copy_overlay_header(paths)
    apply_vgmstream_cli_cmakelists(paths)
    apply_libvgmstream_cmakelists(paths)
    apply_vgmstream_cli_c(paths)
    apply_vgmstream_cli_h(paths)
    apply_vgmstream_cli_utils(paths)


def _git_show_file(repo_root: Path, relative_path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"HEAD:{relative_path.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout
    return None


def export_audit_patch(paths: InjectorPaths) -> Path:
    paths.audit_output.parent.mkdir(parents=True, exist_ok=True)
    diffs: list[str] = []

    for relative_path in TARGET_FILES:
        current_path = paths.repo_root / relative_path
        current_text = current_path.read_text(encoding="utf-8")
        old_text = _git_show_file(paths.repo_root, relative_path)
        fromfile = f"a/{relative_path.as_posix()}" if old_text is not None else "/dev/null"
        tofile = f"b/{relative_path.as_posix()}"

        normalized_old = "" if old_text is None else old_text.replace("\r\n", "\n").replace("\r", "\n")
        normalized_new = current_text.replace("\r\n", "\n").replace("\r", "\n")

        diff = difflib.unified_diff(
            normalized_old.splitlines(),
            normalized_new.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            n=0,
            lineterm="",
        )
        diff_text = "\n".join(sanitize_diff_line(line) for line in diff)
        if diff_text:
            diffs.append(diff_text + "\n")

    write_text(paths.audit_output, "".join(diffs))
    return paths.audit_output
