'''
Created on 14 нояб. 2016 г.

@author: Shelepnev, Dmitry
'''

# -*- coding: utf-8 -*-
import os
import zipfile
#from opds_catalog import settings
from constance import config

MAX_STRUCTURE_BYTES = 64 * 1024
MAX_INP_LINE_BYTES = 1024 * 1024

sAuthor = 'AUTHOR'
sGenre  = 'GENRE'
sTitle  = 'TITLE'
sSeries = 'SERIES'
sSerNo  = 'SERNO'
sFile   = 'FILE'
sSize   = 'SIZE'
sLibId  = 'LIBID'
sDel    = 'DEL'
sExt    = 'EXT'
sDate   = 'DATE'
sLang   = 'LANG'
sInsNo  = 'INSNO'
sFolder = 'FOLDER'
sLibRate= 'LIBRATE'
sKeyWords='KEYWORDS'


class Inpx:
    def __init__(self, inpx_file, append_callback, inpskip_callback = lambda inpx,inp,size:0):
        self.inpx_file = inpx_file
        self.inpx_catalog = os.path.dirname(inpx_file)
        self.inpx_structure = False
        self.inpx_folders = False
        self.inpx_format = []
        self.inpx_archive = False
        self.inpx_arch_fnames = []
        self.inpx_encoding = 'utf-8'
        self.inpx_separator = b'\x04'
        self.inpx_itemseparator = ':'
        self.append_callback = append_callback
        self.inpskip_callback = inpskip_callback
        self.TEST_ZIP = config.SOPDS_INPX_TEST_ZIP
        self.TEST_FILES = config.SOPDS_INPX_TEST_FILES
        self.error = 0       
        
    def parse(self):
        with zipfile.ZipFile(self.inpx_file, "r") as finpx:
            self._parse_archive(finpx)

    def _parse_archive(self, finpx):
        filelist = finpx.namelist()
        if 'structure.info' in filelist:
            self.inpx_structure = True
            with finpx.open('structure.info') as structure_file:
                structure = structure_file.read(MAX_STRUCTURE_BYTES + 1)
            if len(structure) > MAX_STRUCTURE_BYTES:
                raise ValueError('INPX structure.info is too large')
            self.inpx_format = structure.decode('utf-8').split(';')
            self.inpx_folders = sFolder in self.inpx_format
        else:
            self.inpx_format = [sAuthor, sGenre, sTitle, sSeries, sSerNo,
                                sFile, sSize, sLibId, sDel, sExt, sDate, sLang]

        for inp_file in filelist:
            inp_name, inp_ext = os.path.splitext(inp_file)
            if inp_ext.upper() != '.INP':
                continue
            if self.inpskip_callback(self.inpx_file, inp_file,
                                     finpx.getinfo(inp_file).file_size):
                continue

            with finpx.open(inp_file) as finp:
                while line := finp.readline(MAX_INP_LINE_BYTES + 1):
                    if len(line) > MAX_INP_LINE_BYTES:
                        raise ValueError('INPX record is too large')
                    meta_list = line.split(self.inpx_separator)
                    meta_data = {}
                    if not self.inpx_folders:
                        meta_data[sFolder] = '%s.zip' % inp_name

                    for idx, key in enumerate(self.inpx_format):
                        try:
                            if key in [sAuthor, sGenre, sSeries]:
                                meta_data[key] = meta_list[idx].decode(self.inpx_encoding).split(self.inpx_itemseparator)
                                if '' in meta_data[key]:
                                    meta_data[key].remove('')
                            else:
                                meta_data[key] = meta_list[idx].decode(self.inpx_encoding)
                        except IndexError:
                            meta_data[key] = ''

                    if not (meta_data[sDel].strip() in ['', '0']):
                        continue

                    zip_file = os.path.join(self.inpx_catalog, meta_data[sFolder])
                    if (self.TEST_ZIP or self.TEST_FILES) and not os.path.isfile(zip_file):
                        continue
                    if self.TEST_FILES:
                        with zipfile.ZipFile(zip_file, 'r') as book_archive:
                            if '%s.%s' % (meta_data[sFile], meta_data[sExt]) not in book_archive.namelist():
                                continue
                    self.append_callback(self.inpx_file, inp_name, meta_data)
