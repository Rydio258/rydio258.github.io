import os
import re
import datetime

# ============ 配置 ============
MUSIC_DIR = 'music'
OUTPUT_FILE = 'playlist.js'

# 这些顶层目录下的子文件夹会被视为「多乐章作品」
# 名称不区分大小写
WORK_BASED_DIRS = ['classical']
# ==============================

def natural_sort_key(s):
    """自然排序：让 1.mp3、2.mp3、10.mp3 按数字顺序排，而不是字典序"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]

def collect_mp3(dir_path):
    """返回目录下所有 mp3 文件名（自然排序）"""
    if not os.path.isdir(dir_path):
        return []
    files = [f for f in os.listdir(dir_path) if f.lower().endswith('.mp3')]
    return sorted(files, key=natural_sort_key)

def main():
    if not os.path.isdir(MUSIC_DIR):
        print(f'错误：找不到 {MUSIC_DIR} 目录')
        return

    # 顶层子目录 = 电台
    subdirs = [d for d in os.listdir(MUSIC_DIR)
               if os.path.isdir(os.path.join(MUSIC_DIR, d)) and not d.startswith('.')]
    subdirs = sorted(subdirs, key=natural_sort_key)

    if not subdirs:
        print(f'警告：{MUSIC_DIR} 下没有子目录')
        return

    n = len(subdirs)
    work_dirs_lower = [d.lower() for d in WORK_BASED_DIRS]
    stations = []

    for i, subdir in enumerate(subdirs):
        # 均匀分布 pos，范围 10 到 90
        pos = round(10 + (i * 80 / (n - 1))) if n > 1 else 50
        dir_path = os.path.join(MUSIC_DIR, subdir)
        is_work_based = subdir.lower() in work_dirs_lower

        songs = []

        if is_work_based:
            # ============ 多乐章模式 ============
            # 子文件夹 = 作品，里面的 mp3 = 乐章
            work_names = [d for d in os.listdir(dir_path)
                          if os.path.isdir(os.path.join(dir_path, d)) and not d.startswith('.')]
            work_names = sorted(work_names, key=natural_sort_key)

            for work in work_names:
                work_path = os.path.join(dir_path, work)
                movement_files = collect_mp3(work_path)
                if not movement_files:
                    continue
                movements = [f'music/{subdir}/{work}/{mf}' for mf in movement_files]
                songs.append({'work': work, 'movements': movements})

            # 也支持把单曲 mp3 直接放在 classical 根目录（可选）
            loose_files = collect_mp3(dir_path)
            for lf in loose_files:
                songs.append(f'music/{subdir}/{lf}')

        else:
            # ============ 普通模式 ============
            # 目录里的 mp3 = 单曲
            for f in collect_mp3(dir_path):
                songs.append(f'music/{subdir}/{f}')

        if songs:
            stations.append({'pos': pos, 'name': subdir, 'songs': songs})

    if not stations:
        print('警告：没有找到任何歌曲，请检查 music/ 下的结构')
        return

    # ============ 生成 playlist.js ============
    lines = []
    lines.append('// 此文件由脚本自动生成，请勿手动修改')
    lines.append(f'// 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('')
    lines.append('window.PLAYLIST = {')
    lines.append('    stations: [')

    for idx, st in enumerate(stations):
        lines.append('        {')
        lines.append(f"            pos: {st['pos']},")
        name_escaped = st['name'].replace('\\', '\\\\').replace("'", "\\'")
        lines.append(f"            name: '{name_escaped}',")
        lines.append('            songs: [')

        for song_idx, song in enumerate(st['songs']):
            is_last_song = (song_idx == len(st['songs']) - 1)

            if isinstance(song, dict):
                # 多乐章作品
                lines.append('                {')
                work_escaped = song['work'].replace('\\', '\\\\').replace("'", "\\'")
                lines.append(f"                    work: '{work_escaped}',")
                lines.append('                    movements: [')
                for m_idx, mov in enumerate(song['movements']):
                    mov_escaped = mov.replace('\\', '\\\\').replace("'", "\\'")
                    comma = '' if m_idx == len(song['movements']) - 1 else ','
                    lines.append(f"                        '{mov_escaped}'{comma}")
                lines.append('                    ]')
                lines.append(f"                }}{'' if is_last_song else ','}")
            else:
                # 单曲
                song_escaped = song.replace('\\', '\\\\').replace("'", "\\'")
                lines.append(f"                '{song_escaped}'{'' if is_last_song else ','}")

        lines.append('            ]')
        lines.append(f"        }}{'' if idx == len(stations) - 1 else ','}")

    lines.append('    ]')
    lines.append('};')
    lines.append('')

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    # ============ 统计输出 ============
    total_entries = 0
    total_movements = 0
    total_works = 0
    total_singles = 0

    for s in stations:
        for song in s['songs']:
            total_entries += 1
            if isinstance(song, dict):
                total_works += 1
                total_movements += len(song['movements'])
            else:
                total_singles += 1

    print(f'已生成 {OUTPUT_FILE}')
    print(f'  电台数：{len(stations)}')
    print(f'  条目数：{total_entries}（{total_works} 个多乐章作品 + {total_singles} 首单曲）')
    if total_movements:
        print(f'  多乐章文件总数：{total_movements}')
    print('')
    for s in stations:
        works = sum(1 for x in s['songs'] if isinstance(x, dict))
        singles = sum(1 for x in s['songs'] if not isinstance(x, dict))
        tag = []
        if works:
            tag.append(f'{works}作品')
        if singles:
            tag.append(f'{singles}单曲')
        print(f'    [{s["pos"]:>3}] {s["name"]} · {", ".join(tag) or "空"}')


if __name__ == '__main__':
    main()
