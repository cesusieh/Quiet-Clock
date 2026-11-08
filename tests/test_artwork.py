import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from quiet_island.artwork import artwork_path, locate_instance, png_dimensions, PNG


@pytest.mark.parametrize('uri', ['file:///etc/passwd', 'file:///tmp/../etc/passwd',
    'file://remote/tmp/.com.google.Chrome.ABCD',
    'https://example.com/image.png', 'file:///tmp/.com.google.Chrome.ABCD?x=1'])
def test_rejects_unrelated_files(uri):
    with pytest.raises(ValueError):
        artwork_path(uri)


def test_chrome_artwork_path():
    assert artwork_path('file:///tmp/.com.google.Chrome.ABCD') == '/tmp/.com.google.Chrome.ABCD'


def test_rejects_excessive_dimensions():
    data = PNG + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 9000, 1) + bytes(9)
    with pytest.raises(ValueError):
        png_dimensions(data)


def test_proxy_must_share_verified_flatpak_scope(tmp_path):
    proc = tmp_path / 'proc'
    runtime = tmp_path / 'runtime'
    instance = runtime / '.flatpak' / '123'
    instance.mkdir(parents=True)
    (instance / 'info').write_text('[Application]\nname=com.google.Chrome\n')
    (instance / 'pid').write_text('22')
    scope = '0::/app.slice/app-flatpak-com.google.Chrome-123.scope\n'
    for pid in ['11', '22']:
        (proc / pid).mkdir(parents=True)
        (proc / pid / 'cgroup').write_text(scope)
    assert locate_instance(11, runtime, proc) == ('com.google.Chrome', '123')
    (proc / '22' / 'cgroup').write_text('0::/another.scope\n')
    with pytest.raises(ValueError):
        locate_instance(11, runtime, proc)
