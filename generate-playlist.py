#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描 music/ 目录，生成 playlist.js。

排序优先级：
    1. mp3 ID3 标签里的 disc + track 序号（最准）
    2. 文件名开头的数字
    3. 自然排序

classical 目录下的子文件夹会被视为「多乐章作品」，
每个子文件夹作为一个 work，里面的 mp3 作为 movements。

用法：
    python generate-playlist.py
    python generate-playlist.py --show-meta    # 只打印排序结果，不写文件
"""

import os
import re
import sys
import json
import argparse
import datetime
from pathlib import Path

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False
    print('提示：安装 mutagen 可读取 mp3 元数据 → pip install mutagen')


# ==================== 配置 ====================

REPO_DIR = Path(__file__).resolve().parent
MUSIC_DIR = REPO_DIR / 'music'
OUTPUT_FILE = REPO_DIR / 'playlist.js'

# 这些顶层目录下的子文件夹会被视为「多乐章作品」
WORK_BASED_DIRS = ['classical']

# ============================================


# ==================== 自然排序 ====================

def natural_sort_key(s):
    """自然排序：让 1.mp3、2.mp3、10.mp3 按数字顺序排"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


# ==================== 元数据读取 ====================

def read_mp3_meta(path):
    """
    读取 mp3 元数据，返回：
    {
        'track': int | None,
        'disc': int | None,
        'title': str,
        'artist': str,
        'album': str,
        'album_artist': str,
        'year': str,
        'genre': str,
        'composer': str,
        'duration': float,
    }
    """
    meta = {
        'track': None,
        'disc': None,
        'title': '',
        'artist': '',
        'album': '',
        'album_artist': '',
        'year': '',
        'genre': '',
        'composer': '',
        'duration': 0.0,
    }

    if not HAS_MUTAGEN:
        return meta

    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return meta

        if audio.info and hasattr(audio.info, 'length'):
            meta['duration'] = round(audio.info.length, 1)

        def first(key):
            v = audio.get(key)
            if not v:
                return ''
            return str(v[0]).strip()

        meta['title'] = first('title')
        meta['artist'] = first('artist')
        meta['album'] = first('album')
        meta['album_artist'] = first('albumartist')
        meta['year'] = first('date') or first('year')
        meta['genre'] = first('genre')
        meta['composer'] = first('composer')

        trk = first('tracknumber')
        if trk:
            m = re.match(r'^\s*(\d+)', trk)
            if m:
                meta['track'] = int(m.group(1))

        disc = first('discnumber')
        if disc:
            m = re.match(r'^\s*(\d+)', disc)
            if m:
                meta['disc'] = int(m.group(1))

    except Exception as e:
        print(f'  读取元数据失败 {path.name}: {e}')

    return meta


def get_sort_key(path):
    """
    排序 key：
    1. 有 track         → (0, disc, track, 自然名)
    2. 文件名有数字     → (1, 0, number, 自然名)
    3. 都没有           → (2, 0, 0, 自然名)
    """
    meta = read_mp3_meta(path) if HAS_MUTAGEN else {}
    stem = path.stem
    natural = natural_sort_key(stem)

    disc = meta.get('disc')
    track = meta.get('track')

    if track is not None:
        return (0, disc if disc is not None else 1, track, natural)

    m = re.match(r'^\s*(\d+)', stem)
    if m:
        return (1, 0, int(m.group(1)), natural)

    return (2, 0, 0, natural)


def sort_files(paths):
    return sorted(paths, key=get_sort_key)


# ==================== 文件名解析 ====================

def parse_filename(name):
    """元数据缺失时的后备解析"""
    stem = Path(name).stem.strip()
    stem_clean = re.sub(r'^\s*\d+[\s.\-_]+', '', stem).strip()
    if not stem_clean:
        stem_clean = stem

    if ' - ' in stem_clean:
        parts = stem_clean.split(' - ', 1)
        return {'artist': parts[0].strip(), 'title': parts[1].strip()}
    if '-' in stem_clean and ' ' not in stem_clean.split('-')[0]:
        parts = stem_clean.split('-', 1)
        return {'artist': parts[0].strip(), 'title': parts[1].strip()}
    return {'artist': '', 'title': stem_clean}


def get_display_title(path):
    """获取用于显示的曲名，优先用元数据"""
    meta = read_mp3_meta(path) if HAS_MUTAGEN else {}
    title = meta.get('title', '').strip()
    if title:
        return title
    parsed = parse_filename(path.name)
    return parsed['title'] or path.stem


def get_display_artist(path):
    meta = read_mp3_meta(path) if HAS_MUTAGEN else {}
    artist = meta.get('artist', '').strip()
    if artist:
        return artist
    parsed = parse_filename(path.name)
    return parsed['artist']


# ==================== 扫描 ====================

def collect_mp3(dir_path):
    """返回目录下所有 mp3 文件，按元数据排序"""
    if not os.path.isdir(dir_path):
        return []
    files = [Path(dir_path) / f for f in os.listdir(dir_path)
             if f.lower().endswith('.mp3')]
    return sort_files(files)


def scan_stations():
    """
    返回：
    [
        {
            'name': 'C-POP',
            'path': Path,
            'songs': [Path, ...],
            'works': [{'name': str, 'path': Path, 'movements': [Path, ...]}],
            'local_only': bool,
        },
        ...
    ]
    """
    if not MUSIC_DIR.is_dir():
        return []

    work_based_lower = [d.lower() for d in WORK_BASED_DIRS]
    stations = []

    top_dirs = sorted(
        [d for d in MUSIC_DIR.iterdir()
         if d.is_dir() and not d.name.startswith('.')],
        key=lambda p: natural_sort_key(p.name)
    )

    for d in top_dirs:
        is_work_based = d.name.lower() in work_based_lower
        songs = []
        works = []

        if is_work_based:
            # 子文件夹 = 作品
            for sub in sorted(d.iterdir(),
                              key=lambda p: natural_sort_key(p.name)):
                if not sub.is_dir() or sub.name.startswith('.'):
                    continue
                movements = collect_mp3(sub)
                if movements:
                    works.append({
                        'name': sub.name,
                        'path': sub,
                        'movements': movements,
                    })
                # 作品里的 mp3 也加入总列表（用于电台级哈希）
                songs.extend(movements)

            # 根目录下的散装 mp3
            for f in collect_mp3(d):
                songs.append(f)

        else:
            # 普通电台：mp3 是单曲
            for f in collect_mp3(d):
                songs.append(f)

        if songs or works:
            stations.append({
                'name': d.name,
                'path': d,
                'songs': songs,
                'works': works,
                'local_only': is_work_based,
            })

    return stations


# ==================== JS 输出 ====================

def js_escape(s):
    return (s.replace('\\', '\\\\')
             .replace("'", "\\'")
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\r', ''))


def js_string(s):
    return "'" + js_escape(s) + "'"


def path_to_url(path):
    """Path 转成 music/ 开头的相对 URL"""
    rel = path.relative_to(REPO_DIR)
    # 用 / 分隔（Windows 上是 \）
    return str(rel).replace('\\', '/')


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description='生成 playlist.js')
    parser.add_argument('--show-meta', action='store_true',
                        help='只打印排序结果，不写文件')
    args = parser.parse_args()

    if not MUSIC_DIR.is_dir():
        print(f'错误：找不到 {MUSIC_DIR}')
        return

    print('扫描 music/ 目录...')
    stations = scan_stations()
    if not stations:
        print('没有找到任何电台')
        return

    print(f'找到 {len(stations)} 个电台：{[s["name"] for s in stations]}')

    if args.show_meta:
        for station in stations:
            print(f'\n===== {station["name"]} =====')
            if station['works']:
                for work in station['works']:
                    print(f'  ▶ {work["name"]}')
                    for idx, mp3 in enumerate(work['movements'], 1):
                        meta = read_mp3_meta(mp3) if HAS_MUTAGEN else {}
                        track = meta.get('track')
                        disc = meta.get('disc')
                        title = meta.get('title', '')
                        flag = '⚠ ' if track is None and not re.match(r'^\s*\d+', mp3.stem) else '  '
                        info = f'disc {disc or "-"} / track {track or "-"}'
                        print(f'    {flag}{idx:02d}. {mp3.name}')
                        print(f'         {info}' + (f' | {title}' if title else ''))
            if station['songs']:
                # 只显示散装 mp3（作品内的已经显示过）
                loose = [s for s in station['songs']
                         if not any(s.parent == w['path'] for w in station['works'])]
                if loose:
                    print(f'  [散装单曲]')
                    for idx, mp3 in enumerate(loose, 1):
                        meta = read_mp3_meta(mp3) if HAS_MUTAGEN else {}
                        title = meta.get('title', '')
                        print(f'    {idx:02d}. {mp3.name}'
                              + (f' | {title}' if title else ''))
        return

    # ============ 生成 playlist.js ============
    lines = []
    lines.append('// 此文件由脚本自动生成，请勿手动修改')
    lines.append(f'// 生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('')
    lines.append('window.PLAYLIST = {')
    lines.append('    stations: [')

    n = len(stations)

    for idx, st in enumerate(stations):
        # 均匀分布 pos 10~90
        pos = round(10 + (idx * 80 / (n - 1))) if n > 1 else 50

        lines.append('        {')
        lines.append(f'            pos: {pos},')
        lines.append(f'            name: {js_string(st["name"])},')
        lines.append('            songs: [')

        entries = []

        # 多乐章作品
        for work in st['works']:
            movements_urls = [path_to_url(m) for m in work['movements']]
            entries.append({
                'type': 'work',
                'work': work['name'],
                'movements': movements_urls,
            })

        # 散装 mp3（对作品电台，排除已经在作品里的）
        loose_songs = [s for s in st['songs']
                       if not any(s.parent == w['path'] for w in st['works'])]
        for mp3 in loose_songs:
            entries.append({
                'type': 'song',
                'url': path_to_url(mp3),
            })

        for e_idx, entry in enumerate(entries):
            is_last = (e_idx == len(entries) - 1)
            tail = '' if is_last else ','

            if entry['type'] == 'work':
                lines.append('                {')
                lines.append(f'                    work: {js_string(entry["work"])},')
                lines.append('                    movements: [')
                for m_idx, m in enumerate(entry['movements']):
                    m_tail = '' if m_idx == len(entry['movements']) - 1 else ','
                    lines.append(f'                        {js_string(m)}{m_tail}')
                lines.append('                    ]')
                lines.append(f'                }}{tail}')
            else:
                lines.append(f'                {js_string(entry["url"])}{tail}')

        lines.append('            ]')
        lines.append(f'        }}{"" if idx == n - 1 else ","}')

    lines.append('    ]')
    lines.append('};')
    lines.append('')

    OUTPUT_FILE.write_text('\n'.join(lines), encoding='utf-8')

    # ============ 统计 ============
    total_entries = 0
    total_works = 0
    total_singles = 0
    total_movements = 0

    for s in stations:
        for w in s['works']:
            total_entries += 1
            total_works += 1
            total_movements += len(w['movements'])
        loose = [x for x in s['songs']
                 if not any(x.parent == w['path'] for w in s['works'])]
        total_entries += len(loose)
        total_singles += len(loose)

    print(f'\n已生成 {OUTPUT_FILE.name}')
    print(f'  电台数：{len(stations)}')
    print(f'  条目数：{total_entries}（{total_works} 作品 + {total_singles} 单曲）')
    if total_movements:
        print(f'  多乐章文件总数：{total_movements}')
    print('')
    for s in stations:
        works = len(s['works'])
        loose = len([x for x in s['songs']
                     if not any(x.parent == w['path'] for w in s['works'])])
        tag = []
        if works:
            tag.append(f'{works}作品')
        if loose:
            tag.append(f'{loose}单曲')
        print(f'    [{s["name"]}] {", ".join(tag) or "空"}')


if __name__ == '__main__':
    main()
