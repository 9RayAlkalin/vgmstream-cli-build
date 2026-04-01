from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

from vgmstream_cli_build.injector import InjectorPaths, apply_overlay, export_audit_patch


CLI_C = textwrap.dedent(
    """\
    #include <stdio.h>
    #include <getopt.h>
    #ifdef WIN32
    #include <io.h>
    #include <fcntl.h>
    #else
    #include <unistd.h>
    #endif
    #include "vgmstream_cli.h"
    #include "wav_utils.h"
    #include "../version.h"
    #ifndef VGMSTREAM_VERSION
    #define VGMSTREAM_VERSION "unknown version " __DATE__
    #endif
    #define APP_NAME  "vgmstream CLI decoder " VGMSTREAM_VERSION
    #define APP_INFO  APP_NAME " (" __DATE__ ")"

    static void print_usage(const char* progname, bool is_help) {
        fprintf(is_help ? stdout : stderr, APP_INFO "\\n"
                "Usage: %s [-o <outfile.wav>] [options] <infile> ...\\n"
                "Options:\\n"
                "    -o <outfile.wav>: name of output .wav file, default <infile>.wav\\n"
                "       <outfile> wildcards can be ?s=subsong, ?n=stream name, ?f=infile\\n"
                "    -V: print version info and supported extensions as JSON\\n"
                "    -I: print requested file info as JSON\\n"
                "    -h: print all commands\\n"
                , progname);
    }

    static bool parse_config(cli_config_t* cfg, int argc, char** argv) {
        int opt;
        while ((opt = getopt(argc, argv, "+o:l:f:d:ipPcmxeLEFrgb2:s:tTk:K:hOvD:S:B:VIwW:")) != -1) {
            switch (opt) {
                case '2':
                    cfg->stereo_track = atoi(optarg) + 1;
                    break;
                case 'h':
                    print_usage(argv[0], true);
                    break;
            }
        }
        return true;
    }

    static bool convert_file(cli_config_t* cfg) {
        return true;
    }

    static bool convert_subsongs(cli_config_t* cfg) {
        return true;
    }

    int main(int argc, char** argv) {
        cli_config_t cfg = {0};
        bool res, ok;

        for (int i = 1; i < argc; i++) {
            // ignore flags
            if (i < CLI_MAX_FLAGS && cfg.flag_index[i]) {
                continue;
            }

            // current name, to avoid passing params all the time
            cfg.infilename = argv[i];
            if (cfg.outfilename_config)
                cfg.outfilename = NULL;

            if (cfg.subsong_index > 0 && cfg.subsong_end != 0) {
                res = convert_subsongs(&cfg);
                if (res) ok = true;
            }
            else {
                cfg.subsong_current_index = cfg.subsong_index;

                res = convert_file(&cfg);
                if (res) ok = true;
            }
        }

        /* ok if at least one succeeds, for programs that check result code */
        if (!ok)
            goto fail;

        return 0;
    fail:
        return 1;
    }
    """
)

CLI_H = textwrap.dedent(
    """\
    #ifndef _VGMSTREAM_CLI_H_
    #define _VGMSTREAM_CLI_H_

    typedef struct {
        const char* infilename;

        const char* outfilename_config;
        const char* outfilename;

        bool decode_only;
        bool test_reset;
        bool validate_extensions;
        int seek_samples1;
        int seek_samples2;
        int downmix_channels;
        int stereo_track;

        int subsong_index;
        int subsong_end;
        int subsong_current_index;

        bool flag_index[32];
    } cli_config_t;

    #define CLI_PATH_LIMIT 4096
    #define CLI_MAX_FLAGS 32

    #endif
    """
)

CLI_UTILS_C = textwrap.dedent(
    """\
    #include <stdio.h>
    #include <inttypes.h>
    #include "vgmstream_cli.h"
    #include "vjson.h"
    #include "../src/libvgmstream.h"

    static void clean_filename(char* dst, int clean_paths) {
    }

    void replace_filename(char* dst, size_t dstsize, cli_config_t* cfg, libvgmstream_t* vgmstream) {
        int subsong;
        char stream_name[CLI_PATH_LIMIT];
        char buf[CLI_PATH_LIMIT];
        char tmp[CLI_PATH_LIMIT];
        char* pos = buf;

        if (pos[1] == 'n') {
            snprintf(tmp, sizeof(tmp), "%s", stream_name);
        }
        else if (pos[1] == 'f') {
            pos[0] = '%';
            pos[1] = 's'; /* use %s */
            snprintf(tmp, sizeof(tmp), buf, cfg->infilename);
        }
        else if (pos[1] == 's') {
            pos[0] = '%';
            pos[1] = 'i'; /* use %i */
            snprintf(tmp, sizeof(tmp), buf, subsong);
        }
    }
    """
)


def create_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Codex"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "codex@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, check=True)


def test_apply_overlay_and_export_audit(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_root = tmp_path / "upstream"
    overlay_header_source = Path("overlay/cli/virace_cli_ext.h")

    (workspace_root / "overlay/cli").mkdir(parents=True)
    (workspace_root / "overlay/cli/virace_cli_ext.h").write_text(overlay_header_source.read_text(encoding="utf-8"), encoding="utf-8")

    (repo_root / "cli").mkdir(parents=True)
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "version.h").write_text("#define VGMSTREAM_VERSION 1\n", encoding="utf-8")
    (repo_root / "cli/vgmstream_cli.c").write_text(CLI_C, encoding="utf-8")
    (repo_root / "cli/vgmstream_cli.h").write_text(CLI_H, encoding="utf-8")
    (repo_root / "cli/vgmstream_cli_utils.c").write_text(CLI_UTILS_C, encoding="utf-8")
    create_git_repo(repo_root)

    paths = InjectorPaths.resolve(workspace_root=workspace_root, repo_root=repo_root)
    apply_overlay(paths)
    audit_path = export_audit_patch(paths)

    cli_c = (repo_root / "cli/vgmstream_cli.c").read_text(encoding="utf-8")
    cli_h = (repo_root / "cli/vgmstream_cli.h").read_text(encoding="utf-8")
    cli_utils = (repo_root / "cli/vgmstream_cli_utils.c").read_text(encoding="utf-8")
    overlay_copy = (repo_root / "cli/virace_cli_ext.h").read_text(encoding="utf-8")
    audit_text = audit_path.read_text(encoding="utf-8")

    assert '#include "virace_cli_ext.h"' in cli_c
    assert 'case \'Y\':' in cli_c
    assert 'virace_is_directory(argv[i])' in cli_c
    assert "bool delete_source;" in cli_h
    assert "virace_extract_basename_without_extension" in cli_utils
    assert overlay_copy.startswith("#ifndef _VIRACE_CLI_EXT_H_")
    assert "cli/virace_cli_ext.h" in audit_text


def test_apply_overlay_uses_wide_windows_path_helpers(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_root = tmp_path / "upstream"
    overlay_header_source = Path("overlay/cli/virace_cli_ext.h")

    (workspace_root / "overlay/cli").mkdir(parents=True)
    (workspace_root / "overlay/cli/virace_cli_ext.h").write_text(overlay_header_source.read_text(encoding="utf-8"), encoding="utf-8")

    (repo_root / "cli").mkdir(parents=True)
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "version.h").write_text("#define VGMSTREAM_VERSION 1\n", encoding="utf-8")
    (repo_root / "cli/vgmstream_cli.c").write_text(CLI_C, encoding="utf-8")
    (repo_root / "cli/vgmstream_cli.h").write_text(CLI_H, encoding="utf-8")
    (repo_root / "cli/vgmstream_cli_utils.c").write_text(CLI_UTILS_C, encoding="utf-8")
    create_git_repo(repo_root)

    paths = InjectorPaths.resolve(workspace_root=workspace_root, repo_root=repo_root)
    apply_overlay(paths)

    cli_c = (repo_root / "cli/vgmstream_cli.c").read_text(encoding="utf-8")
    overlay_copy = (repo_root / "cli/virace_cli_ext.h").read_text(encoding="utf-8")

    assert "GetFileAttributesW" in overlay_copy
    assert "FindFirstFileW" in overlay_copy
    assert "MultiByteToWideChar" in overlay_copy
    assert "WideCharToMultiByte" in overlay_copy
    assert "_wremove" in overlay_copy
    assert "return remove(path);" in overlay_copy
    assert "virace_remove_path(cfg->infilename)" in cli_c
