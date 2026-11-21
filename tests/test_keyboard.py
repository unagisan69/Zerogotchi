"""Exercise the native editor through the built-in keyboard's evdev codes."""
import re
import runpy
import unittest


UI = runpy.run_path('app/zerogotchi')


class Keyboard(unittest.TestCase):
    def setUp(self):
        self.app = UI['App'].__new__(UI['App'])
        self.app.shift = set()
        self.app.update_editor = lambda *args: None
        self.app.editor = {'value':'','limit':128,'pattern':re.compile('.*'),
                           'commit':lambda value:None,'back':lambda:None}

    def test_all_stock_sym_codes_match_the_printed_symbols(self):
        expected = {26:'!',27:'@',39:'#',40:'$',41:'%',43:'^',51:'&',52:'*',53:'(',94:')',
                    55:'~',69:'`',70:'_',71:'-',72:'+',73:'=',74:'[',75:']',76:'{',77:'}',
                    79:';',80:':',81:"'",82:'"',83:'<',85:'>',86:'\\',89:'|',90:',',91:'.',92:'/',93:'?'}
        for code,symbol in expected.items():
            with self.subTest(code=code):
                self.app.editor['value']=''
                self.app.edit_key(code)
                self.assertEqual(self.app.editor['value'],symbol)
        self.assertEqual(UI['SYMBOLS'],expected)

    def test_reported_sequence_and_shift_are_independent(self):
        for code in [70,71,83,85,72]: self.app.edit_key(code)
        self.assertEqual(self.app.editor['value'],'_-<>+')
        self.app.shift.add(42)
        for code in [70,71,83,85,72]: self.app.edit_key(code)
        self.assertEqual(self.app.editor['value'],'_-<>+_-<>+')
        self.app.edit_key(30)
        self.assertTrue(self.app.editor['value'].endswith('A'))
        self.app.shift.clear();self.app.edit_key(30)
        self.assertTrue(self.app.editor['value'].endswith('Aa'))

    def test_limit_backspace_and_form_validation_are_preserved(self):
        self.app.editor['limit']=1
        self.app.edit_key(70);self.app.edit_key(72)
        self.assertEqual(self.app.editor['value'],'_')
        self.app.edit_key(UI['KEY_BACKSPACE'])
        self.assertEqual(self.app.editor['value'],'')
        self.app.edit_key(72)
        self.app.editor['pattern']=re.compile('[a-z]+')
        editor=self.app.editor
        self.app.edit_key(UI['KEY_ENTER'])
        self.assertIs(self.app.editor,editor)
        self.app.editor['pattern']=re.compile('[+]')
        saved=[];self.app.editor['commit']=saved.append
        self.app.edit_key(UI['KEY_ENTER'])
        self.assertEqual(saved,['+'])


if __name__=='__main__':unittest.main()
