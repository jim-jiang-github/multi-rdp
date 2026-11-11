# -*- coding: utf-8 -*-
"""由 tag 生成 PyInstaller 的版本资源文件，让 exe 属性里有版本号。

用法：
    python scripts/make_version.py --tag v1.2.3 --out build/version_info.txt

tag 支持 v1.2.3 / 1.2.3 / v1.2.3-rc1 这几种写法；解析不出数字时退回 0.0.0，
不因为 tag 名字怪就让构建挂掉。

MultiRDP.spec 通过环境变量 MULTIRDP_VERSION_FILE 引用这个文件；本地
build.bat 不设该变量，打出来的 exe 就没有版本资源（和以前一样）。
"""

import argparse
import os
import re

# exe 属性里显示的文案，改这里就行
PRODUCT = 'MultiRDP'
DESCRIPTION = '多用户远程桌面管理工具'
COPYRIGHT = 'Copyright (c) 2026 Jim Jiang'


def _ver_tuple(tag):
    """从 tag 里抠出最多四段数字，不足补 0。"""
    m = re.search(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?', tag or '')
    nums = [int(x) for x in (m.groups() if m else ()) if x]
    nums = (nums + [0, 0, 0, 0])[:4]
    return tuple(nums)


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
    args = ap.parse_args()

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
