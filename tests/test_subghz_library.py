"""Library import fixtures use generated signals and never contact RF hardware."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

import subghz
import subghz_library as library
import wifi

RAW = 'Filetype: Flipper SubGhz RAW File\nVersion: 1\nFrequency: 433920000\nPreset: FuriHalSubGhzPresetOok270Async\nProtocol: RAW\nRAW_Data: 350 -1050\nRAW_Data: 1050 -350\n'


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as output:
        for name,content in entries:
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))


class Library(unittest.TestCase):
    def test_parser_and_rejections(self):
        data = subghz.parse_flipper_raw(RAW)
        self.assertEqual(data['frequency'], 433.92)
        self.assertEqual(data['pulses'], [350,-1050,1050,-350])
        for text in (RAW.replace('Ook270','2FSKDev238'), RAW.replace('Version: 1','Version: 2'),
                     RAW.replace('Protocol: RAW','Protocol: BinRAW'), RAW+'Version: 1\n',
                     RAW.replace('350 -1050','0 -1050')):
            with self.assertRaises(wifi.WifiError): subghz.parse_flipper_raw(text)

    def test_import_filters_formats_paths_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); bundle=base/'source.tgz'; destination=base/'out'
            archive(bundle,[('source/subghz/LED/test.sub',RAW),
                            ('source/subghz/LED/unsupported.sub',RAW.replace('Protocol: RAW','Protocol: Princeton')),
                            ('source/subghz/Jamming/test.sub',RAW),
                            ('source/subghz/deBruijn/test.sub',RAW),
                            ('source/subghz/LED/../../escape.sub',RAW)])
            updates=[]
            result=library.unpack(bundle,destination,threading.Event(),updates.append)
            self.assertEqual(result['imported'],1)
            self.assertEqual(result['unsupported'],1)
            self.assertEqual(result['excluded'],3)
            self.assertFalse((base/'escape.sub').exists())
            self.assertEqual(subghz.load_record(destination/'LED/test.sub')['frequency'],433.92)
            self.assertEqual((destination/'LED/test.sub').stat().st_mode & 0o777,0o600)
            (destination/'LED/test.sub').write_text('my local replacement')
            result=library.unpack(bundle,destination,threading.Event(),updates.append)
            self.assertEqual(result['existing'],1)
            self.assertEqual((destination/'LED/test.sub').read_text(),'my local replacement')
            self.assertTrue(all(isinstance(status,library.Progress) for status in updates))

    def test_symlink_and_cancel(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary); destination=base/'out';destination.mkdir()
            outside=base/'outside';outside.mkdir();(destination/'LED').symlink_to(outside)
            bundle=base/'source.tgz';archive(bundle,[('source/subghz/LED/test.sub',RAW)])
            result=library.unpack(bundle,destination,threading.Event(),lambda _:None)
            self.assertEqual(result['excluded'],1);self.assertFalse(list(outside.iterdir()))
            stop=threading.Event();stop.set()
            with self.assertRaisesRegex(wifi.WifiError,'cancelled'):
                library.unpack(bundle,destination,stop,lambda _:None)

    def test_download_failure_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(wifi,'DATA',Path(temporary)):
            with patch.object(library.urllib.request,'urlopen',side_effect=library.urllib.error.URLError('offline')):
                with self.assertRaisesRegex(wifi.WifiError,'download failed'):
                    library.download_import(threading.Event(),lambda _:None)
            self.assertFalse(any(library.root().glob('.download-*')))

    def test_download_complete_and_no_radio_access(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(wifi,'DATA',Path(temporary)):
            bundle=Path(temporary)/'fixture.tgz';archive(bundle,[('source/subghz/LED/test.sub',RAW)])
            class Response(io.BytesIO):
                headers={'Content-Length':str(bundle.stat().st_size)}
            with patch.object(library.urllib.request,'urlopen',return_value=Response(bundle.read_bytes())),patch.object(subghz,'receiver') as radio:
                result=library.download_import(threading.Event(),lambda _:None)
                self.assertEqual(result['imported'],1)
                radio.assert_not_called()
            self.assertTrue((library.root()/'.last-import.json').is_file())
            self.assertFalse(any(library.root().glob('.download-*')))

    def test_cancel_before_download_and_incomplete_response_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(wifi,'DATA',Path(temporary)):
            stop=threading.Event();stop.set()
            with patch.object(library.urllib.request,'urlopen') as network:
                with self.assertRaisesRegex(wifi.WifiError,'cancelled'):
                    library.download_import(stop,lambda _:None)
                network.assert_not_called()
            class Response(io.BytesIO): headers={'Content-Length':'1000'}
            with patch.object(library.urllib.request,'urlopen',return_value=Response(b'short')):
                with self.assertRaisesRegex(wifi.WifiError,'interrupted'):
                    library.download_import(threading.Event(),lambda _:None)
            self.assertFalse(any(library.root().glob('.download-*')))

    def test_menu_opt_in_and_pagination(self):
        ui=runpy.run_path('app/zerogotchi');app=ui['App'].__new__(ui['App'])
        with tempfile.TemporaryDirectory() as temporary,patch.object(wifi,'DATA',Path(temporary)):
            app.subghz_library()
            self.assertEqual([item[0] for item in app.items],['Import from GitHub','Browse Library','Not now'])
            for n in range(81): (library.root()/f'{n:03}.sub').write_text(RAW)
            app.subghz_library_browse();self.assertEqual(len(app.items),81)
            self.assertEqual(app.items[-1][0],'Next page')
            app.items[-1][1]();self.assertEqual(len(app.items),2)
            self.assertEqual(app.items[-1][0],'Previous page')


if __name__=='__main__':unittest.main()
