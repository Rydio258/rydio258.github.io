import os
import datetime

MUSIC_DIR = 'music'
OUTPUT_FILE = 'playlist.js'

def main():
    if not os.path.isdir(MUSIC_DIR):
        print(f'错误：找不到 {MUSIC_DIR} 目录')
        return

    # 获取所有子目录（忽略隐藏目录）
    subdirs = [d for d in os.listdir(MUSIC_DIR)
               if os.path.isdir(os.path.join(MUSIC_DIR, d)) and not d.startswith('.')]
    subdirs.sort()

    if not subdirs:
        print(f'警告：{MUSIC_DIR} 下没有子目录')
        return

    n = len(subdirs)
    stations = []

    for i, subdir in enumerate(subdirs):
        # 均匀分布 pos，范围 10 到 90
        pos = round(10 + (i * 80 / (n - 1))) if n > 1 else 50

        dir_path = os.path.join(MUSIC_DIR, subdir)
        songs = []
        for f in sorted(os.listdir(dir_path)):
            if f.lower().endswith('.mp3'):
                songs.append(f'music/{subdir}/{f}')

        if songs:
            stations.append({'pos': pos, 'name': subdir, 'songs': songs})

    # 生成 playlist.js
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
        for song in st['songs']:
            song_escaped = song.replace('\\', '\\\\').replace("'", "\\'")
            lines.append(f"                '{song_escaped}',")
        lines.append('            ]')
        lines.append('        }' + (',' if idx < len(stations) - 1 else ''))

    lines.append('    ]')
    lines.append('};')
    lines.append('')

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    total = sum(len(s['songs']) for s in stations)
    print(f'已生成 {OUTPUT_FILE}')
    print(f'  电台数：{len(stations)}')
    print(f'  歌曲数：{total}')

if __name__ == '__main__':
    main()