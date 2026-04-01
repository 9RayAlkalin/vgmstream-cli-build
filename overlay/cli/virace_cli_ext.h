#ifndef _VIRACE_CLI_EXT_H_
#define _VIRACE_CLI_EXT_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef WIN32
#include <windows.h>
#include <wchar.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#endif

typedef struct {
    char** items;
    int count;
    int capacity;
} virace_file_list_t;

#ifdef _WIN32
#define VIRACE_ARRAY_COUNT(array) (sizeof(array) / sizeof((array)[0]))

static bool virace_utf8_to_wide(const char* input, wchar_t* output, size_t output_len) {
    if (!input || !output || output_len == 0)
        return false;

    return MultiByteToWideChar(CP_UTF8, 0, input, -1, output, (int)output_len) > 0;
}

static bool virace_wide_to_utf8(const wchar_t* input, char* output, size_t output_len) {
    if (!input || !output || output_len == 0)
        return false;

    return WideCharToMultiByte(CP_UTF8, 0, input, -1, output, (int)output_len, NULL, NULL) > 0;
}

static int virace_remove_path(const char* path) {
    wchar_t wpath[CLI_PATH_LIMIT];

    if (!virace_utf8_to_wide(path, wpath, VIRACE_ARRAY_COUNT(wpath)))
        return -1;

    return _wremove(wpath);
}
#endif

static bool virace_is_directory(const char* path) {
#ifdef _WIN32
    wchar_t wpath[CLI_PATH_LIMIT];
    DWORD attrs;

    /* 上游 Windows CLI 已把 argv 转成 UTF-8，这里必须继续走 W API。 */
    if (!virace_utf8_to_wide(path, wpath, VIRACE_ARRAY_COUNT(wpath)))
        return false;

    attrs = GetFileAttributesW(wpath);
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY);
#else
    struct stat st;
    if (stat(path, &st) != 0)
        return false;
    return S_ISDIR(st.st_mode);
#endif
}

static bool virace_has_wem_extension(const char* path) {
    const char* ext = strrchr(path, '.');
    return ext && strcmp(ext, ".wem") == 0;
}

static char* virace_duplicate_path(const char* path) {
    size_t path_len = strlen(path) + 1;
    char* path_copy = malloc(path_len);
    if (!path_copy)
        return NULL;

    memcpy(path_copy, path, path_len);
    return path_copy;
}

static bool virace_append_input_file(virace_file_list_t* file_list, const char* path) {
    if (file_list->count >= file_list->capacity) {
        int new_capacity = file_list->capacity > 0 ? file_list->capacity * 2 : 16;
        char** new_items = realloc(file_list->items, new_capacity * sizeof(*new_items));
        if (!new_items)
            return false;

        file_list->items = new_items;
        file_list->capacity = new_capacity;
    }

    file_list->items[file_list->count] = virace_duplicate_path(path);
    if (!file_list->items[file_list->count])
        return false;

    file_list->count++;
    return true;
}

static void virace_free_input_file_list(virace_file_list_t* file_list) {
    for (int i = 0; i < file_list->count; i++) {
        free(file_list->items[i]);
    }
    free(file_list->items);

    file_list->items = NULL;
    file_list->count = 0;
    file_list->capacity = 0;
}

static bool virace_collect_directory_files(const char* dirpath, virace_file_list_t* file_list) {
    char path[CLI_PATH_LIMIT];
#ifdef _WIN32
    char filename[CLI_PATH_LIMIT];
    wchar_t wdirpath[CLI_PATH_LIMIT];
    wchar_t wsearch[CLI_PATH_LIMIT];
    wchar_t wpath[CLI_PATH_LIMIT];
    WIN32_FIND_DATAW ffd;
    HANDLE hFind = INVALID_HANDLE_VALUE;
    int written;

    if (!virace_utf8_to_wide(dirpath, wdirpath, VIRACE_ARRAY_COUNT(wdirpath)))
        return false;

    written = swprintf(wsearch, VIRACE_ARRAY_COUNT(wsearch), L"%ls\\*", wdirpath);
    if (written < 0 || (size_t)written >= VIRACE_ARRAY_COUNT(wsearch))
        return false;

    hFind = FindFirstFileW(wsearch, &ffd);
    if (hFind == INVALID_HANDLE_VALUE)
        return false;

    do {
        if (ffd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (wcscmp(ffd.cFileName, L".") == 0 || wcscmp(ffd.cFileName, L"..") == 0)
                continue;

            written = swprintf(wpath, VIRACE_ARRAY_COUNT(wpath), L"%ls\\%ls", wdirpath, ffd.cFileName);
            if (written < 0 || (size_t)written >= VIRACE_ARRAY_COUNT(wpath) || !virace_wide_to_utf8(wpath, path, sizeof(path))) {
                FindClose(hFind);
                return false;
            }

            if (!virace_collect_directory_files(path, file_list)) {
                FindClose(hFind);
                return false;
            }
        }
        else if (virace_wide_to_utf8(ffd.cFileName, filename, sizeof(filename)) && virace_has_wem_extension(filename)) {
            written = swprintf(wpath, VIRACE_ARRAY_COUNT(wpath), L"%ls\\%ls", wdirpath, ffd.cFileName);
            if (written < 0 || (size_t)written >= VIRACE_ARRAY_COUNT(wpath) || !virace_wide_to_utf8(wpath, path, sizeof(path))) {
                FindClose(hFind);
                return false;
            }

            if (!virace_append_input_file(file_list, path)) {
                FindClose(hFind);
                return false;
            }
        }
    } while (FindNextFileA(hFind, &ffd) != 0);

    FindClose(hFind);
#else
    DIR* dir = opendir(dirpath);
    struct dirent* entry;

    if (!dir)
        return false;

    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0)
            continue;

        snprintf(path, sizeof(path), "%s/%s", dirpath, entry->d_name);

        if (virace_is_directory(path)) {
            if (!virace_collect_directory_files(path, file_list)) {
                closedir(dir);
                return false;
            }
        }
        else if (virace_has_wem_extension(entry->d_name)) {
            if (!virace_append_input_file(file_list, path)) {
                closedir(dir);
                return false;
            }
        }
    }

    closedir(dir);
#endif

    return true;
}

static void virace_extract_basename_without_extension(const char* input_path, char* basename, size_t basename_size) {
    const char* p_start = strrchr(input_path, '/');
    char* p_end;

    if (!p_start)
        p_start = strrchr(input_path, '\\');
    if (p_start)
        p_start += 1;
    else
        p_start = input_path;

    snprintf(basename, basename_size, "%s", p_start);
    p_end = strrchr(basename, '.');
    if (p_end != NULL && p_end != basename) {
        *p_end = '\0';
    }
}

static void virace_extract_parent_path(const char* input_path, char* path, size_t path_size) {
    const char* p_end = strrchr(input_path, '/');

    if (!p_end)
        p_end = strrchr(input_path, '\\');

    if (p_end) {
        size_t path_len = (size_t)(p_end - input_path + 1);
        snprintf(path, path_size, "%.*s", (int)path_len, input_path);
    }
    else {
        path[0] = '\0';
    }
}

#endif
