# -*- coding: utf-8 -*-
"""由 tag 生成 PyInstaller 的版本资源文件，让 exe 属性里有版本号。

用法：
    python scripts/make_version.py --tag v1.2.3 --out build/version_info.txt
    python scripts/make_version.py --check-tag v1.2.3      # 只校验，不作文件

tag 规则只有一处定义（src/updater.py 的 TAG_RE），运行期自动升级也用它判断
「远端版本比本机新吗」，所以构建和升级不会各认一套格式。

必须是 vMAJOR.MINOR.PATCH，可带 -alpha.1 / -beta.2 / -rc.1 后缀：
    v0.2.0  v1.0.0  v1.3.0-rc.1
不带 v 前缀（1.2.3）、少一段（v1.2）、多一段（v1.2.3.4）都会被拒绝——发布流水线
只监听 v* 标签，放行这些写法只会让人以为发布成功了。

`dev-xxxxxxx` 是手动触发构建用的临时 tag，不算发布，按 0.0.0 打包（运行期会把自己
当成开发版，不参与自动升级）。

MultiRDP.spec 通过环境变量 MULTIRDP_VERSION_FILE 引用这个文件；本地
build.bat 不设该变量，打出来的 exe 就没有版本资源（和以前一样）。
"""

import argparse
import os
import re
import sys

# 版本号规则以运行期为准，避免构建和升级两套正则各说各话
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from updater import parse_tag  # noqa: E402

# exe 属性里显示的文案，改这里就行
PRODUCT = 'MultiRDP'
DESCRIPTION = '多用户远程桌面管理工具'
COPYRIGHT = 'Copyright (c) 2026 Jim Jiang'

HINT = ('tag 必须是 v<主>.<次>.<修>，可带 -alpha.N / -beta.N / -rc.N，'
        '例如 v0.2.0、v1.0.0、v1.3.0-rc.1')


def _ver_tuple(tag):
    """从 tag 里抠出最多四段数字，不足补 0。"""
    m = re.search(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?', tag or '')
    nums = [int(x) for x in (m.groups() if m else ()) if x]
    nums = (nums + [0, 0, 0, 0])[:4]
    return tuple(nums)


def is_dev_tag(tag):
    return (tag or '').startswith('dev-')


def check_tag(tag):
    """合法返回 None，不合法返回错误说明。dev-* 视为非发布构建。"""
    if not tag or is_dev_tag(tag):
        return None
    if parse_tag(tag) is None:
        return '不认识的 tag：%s —— %s' % (tag, HINT)
    return None


def build_text(tag):
    v = _ver_tuple(tag)
    joined = '.'.join(str(x) for x in v[:3])
    if v[3]:
        joined += '.%d' % v[3]
    return """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(v)s,
    prodvers=%(v)s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', %(company)r),
         StringStruct('FileDescription', %(desc)r),
         StringStruct('FileVersion', %(joined)r),
         StringStruct('InternalName', %(product)r),
         StringStruct('LegalCopyright', %(copy)r),
         StringStruct('OriginalFilename', 'MultiRDP.exe'),
         StringStruct('ProductName', %(product)r),
         StringStruct('ProductVersion', %(joined)r)])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {
        'v': repr(v),
        'joined': joined,
        'company': PRODUCT,
        'desc': DESCRIPTION,
        'product': PRODUCT,
        'copy': COPYRIGHT,
    }


def main():
    ap = argparse.ArgumentParser(description='生成 PyInstaller 版本资源文件')
    ap.add_argument('--tag', default='', help='git tag，例如 v1.2.3')
    ap.add_argument('--out', default='build/version_info.txt', help='输出路径')
    ap.add_argument('--check-tag', metavar='TAG', default=None,
                    help='只校验 tag 格式；不合法时退出码 2')
    args = ap.parse_args()

    if args.check_tag is not None:
        err = check_tag(args.check_tag)
        if err:
            sys.stderr.write('::error::%s\n' % err)
            sys.exit(2)
        print('tag ok: %s' % args.check_tag)
        return

    err = check_tag(args.tag)
    if err:
        sys.stderr.write('::error::%s\n' % err)
        sys.exit(2)

    text = build_text(args.tag)
    out = os.path.abspath(args.out)
    d = os.path.dirname(out)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print('version info -> %s  (tag=%s, version=%s)'
          % (args.out, args.tag or '-', '.'.join(str(x) for x in _ver_tuple(args.tag))))


if __name__ == '__main__':
    main()
