// ==================== 电台歌单配置 ====================
// 每个电台包含：
//   pos   — 指针在刻度盘上的位置，0（最左）到 100（最右）
//   name  — 电台栏目名，会显示在频率旁的小栏里
//   songs — 歌曲文件路径数组，只写路径，显示时会自动去掉后缀
//
// 提示：每个电台的 pos 间隔最好大于 10，避免互相干扰。

window.PLAYLIST = {
    stations: [
        {
            pos: 10,
            name: 'C-POP',
            songs: [
                'music/C-POP/djt.mp3'
            ]
        },
        {
            pos: 40,
            name: 'CLASSICAL',
            songs: [
                'music/jj/江南.mp3',
                'music/jj/曹操.mp3'
            ]
        },
        {
            pos: 60,
            name: 'ROCK',
            songs: [
                'music/eason/十年.mp3',
                'music/eason/浮夸.mp3'
            ]
        },
        {
            pos: 85,
            name: 'R&B',
            songs: [
                'music/classical/卡农.mp3',
                'music/classical/月光.mp3'
            ]
        }
    ]
};
