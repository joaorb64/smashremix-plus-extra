#!/usr/bin/env python

#-------------------------------------------------------------------------------
# Name:        Super Smash Bros. File Inserter
# Purpose:     Corrects both hardcoded and tablular file offsets.
# Required:    Python 3.3, pypng module
#
# Author:      Zoinkity (nefariousdogooder@yahoo.com)
#
# Created:     07/22/2014
# Copyright:   (c) Zoinkity 2014
# Licence:     <unlicenced>
#-------------------------------------------------------------------------------

try:
    from tkinter import *
    from tkinter import filedialog
except ImportError:         # (no Tk, e.g. Blender's Python: only the GUI below needs it, not the ROM reader)
    Frame = object

def_exts = (("All Files","*"),)
bin_exts = (('Binary File', '*.bin'),)
req_exts = (('Idx Table', '*.req'),) + bin_exts
rom_exts = (("N64 File","*.n64"),("N64 File","*.v64"),("N64 File","*.z64"),("N64 File","*.rom"),) + bin_exts
csv_exts = (('Tab-Delineated Table', '*.csv'),('Text File', '*.txt'),)

##from VPW2_defaults import fileattr

# The idx table is a convenience.  This should be list of the original, uneditted ROM addresses for files.
##fileidx = tuple(i[0] for i in fileattr)

##from collections import OrderedDict
NALE = (("filetable.bin", (0x41F08, (0x527E4, 0x527F4),(0x10B224, 0x10B238),(0x10B374, 0x10B388),
            (0x10B924, 0x10B938),(0x1134B4, 0x1134C8),(0x116EF4, 0x116F08),(0x1197C4, 0x1197D4),
            (0x119C94, 0x119CA8),(0x11A820, 0x11A830),(0x11C210, 0x11C224),(0x11D9A0, 0x11D9B0),
            (0x11EFB0, 0x11EFC0),(0x120C20, 0x120C30),(0x1221B8, 0x1221C8),(0x124F78, 0x124F88),
            (0x127CE8, 0x127CF8),(0x129634, 0x129644),(0x12A148, 0x12A158),(0x12AB84, 0x12AB98),
            (0x12DE7C, 0x12DE90),(0x12FB30, 0x12FB44),(0x13934C, 0x139360),(0x140538, 0x14054C),
            (0x147290, 0x1472A4),(0x14CD0C, 0x14CD20),(0x14FE78, 0x14FE8C),(0x157D14, 0x157D28),
            (0x15D3CC, 0x15D3DC),(0x15FE7C, 0x15FE90),(0x1652DC, 0x1652EC),(0x16642C, 0x166440),
            (0x1666F4, 0x166708),(0x167834, 0x167848),(0x168934, 0x168948),(0x169AE4, 0x169AF8),
            (0x16AC14, 0x16AC28),(0x16BD14, 0x16BD28),(0x16CE74, 0x16CE88),(0x16DFD4, 0x16DFE8),
            (0x16F9B0, 0x16F9C4),(0x17034C, 0x170360),(0x171044, 0x171058),(0x171ECC, 0x171EE0),
            (0x17288C, 0x1728A0),(0x1736E0, 0x1736F4),(0x17466C, 0x174680),(0x174944, 0x174958),
            (0x175C54, 0x175C68),(0x177138, 0x177148),(0x178274, 0x178288),(0x17A9BC, 0x17A9D0),
            (0x17DF18, 0x17DF2C),(0x17E6D4, 0x17E6E8),(0x17EE88, 0x17EE9C),(0x1820E0, 0x1820F0),
            (0x18868C, 0x18869C),(0x18BC54, 0x18BC68),(0x18CE04, 0x18CE18),(0x18D168, 0x18D178),
            )),
        ("spr_bank-1", ((0x78AB8, 0x78AE0),)),
        ("img_bank-1", ((0x78ABC, 0x78AD8),(0x78AC0, 0x78AD4),)),
        ("spr_bank-2", ((0x78AC4, 0x78AD0),0x9EED0,)),
        ("img_bank-2", (0x9EED4, 0x9EED8)),
        ("spr_bank-3", (0x9EEDC, 0xA2EF0)),
        ("img_bank-3", (0xA2EF4, 0xA2EF8)),
        ("spr_bank-4", (0x9C220, 0xA2EFC)),
        ("img_bank-4", (0x9C224, 0x9C228)),
        ("spr_bank-5", (0x9C22C, (0xE8994, 0xE89B8),)),
        ("img_bank-5", ((0xE8998, 0xE89B0),(0xE899C, 0xE89AC),)),
        ("spr_bank-6", ((0x81E88, 0x81EBC),(0xE89A0, 0xE89A8),)),
        ("img_bank-6", ((0x81E8C, 0x81EB4),(0x81E90, 0x81EB0),)),
        ("spr_bank-7", ((0x81E94, 0x81EAC),(0x862DC, 0x86300),)),
        ("img_bank-7", ((0x862E0, 0x862F8),(0x862E4, 0x862F4),)),
        ("spr_bank-8", ((0x8441C, 0x8443C),(0x862E8, 0x862F0),)),
        ("img_bank-8", ((0x84420, 0x84434),(0x84424, 0x84430),)),
        ("spr_bank-9", ((0x84428, 0x8442C),(0x1194A8, 0x1194C4),)),
        ("img_bank-9", ((0x1194AC, 0x1194C0),(0x1194B0, 0x1194BC),)),
        ("S1_music.sbk", (0x3D768, (0x1194B4, 0x1194B8),)),
        ("B1_sounds1.ctl", (0x3D75C,)),
        ("B1_sounds1.tbl", (0x3D760, 0x3D764,)),
        ("B1_sounds2.ctl", (0x3D750,)),
        ("B1_sounds2.tbl", (0x3D754, 0x3D758,)),
        ("sounds3.rates", (0x3D78C,)),
        ("sounds3.seq", (0x3D790, 0x3D794,)),
        ("sounds4.seq", (0x3D798, 0x3D79C,)),
        ("EOF", (0x3D7A0,)),
        )

NALEnum = ((0x527E8, 0x527F8),(0x10B228, 0x10B23C),(0x10B378, 0x10B38C),
    (0x10B928, 0x10B93C),(0x1134B8, 0x1134CC),(0x116EF8, 0x116F0C),(0x1197C8, 0x1197D8),
    (0x119C98, 0x119CAC),(0x11A824, 0x11A834),(0x11C214, 0x11C228),(0x11D9A4, 0x11D9B4),
    (0x11EFB4, 0x11EFC4),(0x120C24, 0x120C34),(0x1221BC, 0x1221CC),(0x124F7C, 0x124F8C),
    (0x127CEC, 0x127CFC),(0x129638, 0x129648),(0x12A14C, 0x12A15C),(0x12AB88, 0x12AB9C),
    (0x12DE80, 0x12DE94),(0x12FB34, 0x12FB48),(0x139350, 0x139364),(0x14053C, 0x140550),
    (0x147294, 0x1472A8),(0x14CD10, 0x14CD24),(0x14FE7C, 0x14FE90),(0x157D18, 0x157D2C),
    (0x15D3D0, 0x15D3E0),(0x15FE80, 0x15FE94),(0x1652E0, 0x1652F0),(0x166430, 0x166444),
    (0x1666F8, 0x16670C),(0x167838, 0x16784C),(0x168938, 0x16894C),(0x169AE8, 0x169AFC),
    (0x16AC18, 0x16AC2C),(0x16BD18, 0x16BD2C),(0x16CE78, 0x16CE8C),(0x16DFD8, 0x16DFEC),
    (0x16F9B4, 0x16F9C8),(0x170350, 0x170364),(0x171048, 0x17105C),(0x171ED0, 0x171EE4),
    (0x172890, 0x1728A4),(0x1736E4, 0x1736F8),(0x174670, 0x174684),(0x174948, 0x17495C),
    (0x175C58, 0x175C6C),(0x17713C, 0x17714C),(0x178278, 0x17828C),(0x17A9C0, 0x17A9D4),
    (0x17DF1C, 0x17DF30),(0x17E6D8, 0x17E6EC),(0x17EE8C, 0x17EEA0),(0x1820E4, 0x1820F4),
    (0x188690, 0x1886A0),(0x18BC58, 0x18BC6C),(0x18CE08, 0x18CE1C),(0x18D16C, 0x18D17C),
    )

def fetch(n64, p):
    if isinstance(p, int):
        return int.from_bytes(n64.rom[p:p+4], 'big')
    else:
        return n64.getASMvalue(p[0], p[1])
def process(n64, p, dz):
    if isinstance(p, int):
        v = int.from_bytes(n64.rom[p:p+4], 'big') + dz
        n64.rom[p:p+4] = v.to_bytes(4, 'big')
    else:
        v = n64.getASMvalue(p[0], p[1]) + dz
        n64.setASMvalue(v, p[0], p[1])

def ReplaceData(n64, key, data):
    """Replaces <data> at <key> in N64 object,
        updating all subsequent hardcoded file positions.
    The <key> may be an integer index or str codeword for the entry."""
    # TODO: design decision: keep insert or rebuild all?
    if n64.cmp(region='E', version=0):
        tbl = NALE
    else:
        raise NotImplementedError("{} not yet supported!".format(str(n64)))

    if isinstance(key, str):
        from operator import itemgetter
        key = list(map(itemgetter(0), tbl)).index(key)
    if key==len(tbl)-1:
        return

    # Pull the selected and following entry to determine original data size.
    s = fetch(n64, tbl[key][1][0])
    e = fetch(n64, tbl[key+1][1][0])
    sz= e-s
    dz= len(data)-sz
    # Replace the slice with the new data.
    n64.rom[s:e] = data
    # Correct all following hardcoded offsets.
    for entry in tbl[key+1:]:
        for i in entry[1]:
            process(n64, i, dz)

class Patch():
    def __init__(self, **kwargs):
        """Accepts:
            name:   string identifying patch, usually similiar to instance name
            desc:   short description of contents
            code:   container class with (position, bytes) pairs
        """
        d = dict(name='No Name', desc='No description.', code=())
        d.update(kwargs)
        self.__dict__.update(d)

    def __str__(self):
        return "Patch: {}\n\t{}".format(self.name, self.desc)

    def __repr__(self):
        # Any non-required dictionary items should be found.
        t = self.__dict__.copy()
        t.pop('name', None)
        t.pop('desc', None)
        t.pop('code', None)
        d = ''.join([" {}={},".format(i,t.get(i)) for i in t])
        # Hex addresses are a personal preference.
        s = ''.join(["(0x{:X}, {}),".format(i[0], i[1]) for i in self.code])
        return "Patch(name={}, desc={}, code=({}),{})".format(self.name.__repr__(), self.desc.__repr__(), s, d)

    def apply(self, rom):
        """Copies bytes patch to pos in rom.
        """
        if isinstance(rom, bytes):
            rom = bytearray(rom)

        for pos, data in self.code:
            end = pos + len(data)
            h = hash(bytes(rom[pos:end]))

            if h==hash(data):
                print("Patch already applied--skipping.")
                break
            else:
                rom[pos:end] = data
        else:
            print("Patch applied.")
        return rom


def FinishROM(n64, pad=False):
    """Convenience for CIC correction and
        optionally padding the output file.
    Accepts an N64 object, modifying in-place.
    """
    if n64.cmp(region='E', version=0):
        tbl = NALE
    else:
        raise NotImplementedError("{} not yet supported!".format(str(n64)))

    # Last key hold EOF mark.
    s = tbl[-1][1][0]
    if isinstance(s, int):
        pos = int.from_bytes(n64.rom[s:s+4], 'big')
    else:
        pos = n64.getASMvalue(s[0], s[1])
    e = (pos+15) & ~15

    if e>0x4000000:
        print("Warning! Your ROM exceeds 512Mb.  It will not run on console.")

    # Pad pos to 0x16, in conformance with the standard (though not 100% necessary).
    b = b'\xb6U#\x950\xec+\x8d\xb6U#\x950\xec+\x8d'
    n64.rom[pos:e] = b[pos&15:]
    n64.rom = n64.rom[:e]

    # If pad=True, pad the ROM up to the nearest 0x400000 boundry.
    if pad:
        p = e+0x3FFFFF
        p&= ~0x3FFFFF
        from itertools import repeat
        n64.rom.extend(repeat(255, p-e))
    # Recalcs the checksum in-place.
    print("New CRC: {0:08X} {1:08X}".format(*n64.calccrc('6103', True)))


def ConfirmEdit(data, idx):
    """TODO!
    Tests files against hashes to confirm they have editted content.
    Nevermind that testing decompressed files makes more sense.
    The overhead involved in decompressing all of them is a bit much.
    """
    import hashlib
    i = fileidx.index(idx)
    return hashlib.md5(data).digest()==bytes.fromhex(fileattr[i][1])




class SSBtbl(list):
    def __setitem__(self, idx, *args, **kwargs):
        super().__setitem__(idx, tblentry(*args, **kwargs))

    def __str__(self):
        return ", ".join(map(str, self))

    def insert(self, idx, *args, **kwargs):
        super().insert(idx, tblentry(*args, **kwargs))

    def append(self, *args, **kwargs):
        super().append(tblentry(*args, **kwargs))

    @classmethod
    def fromROM(cls, n64, pos=None, num=None):
        """Accepts an N64 object and returns a list of
            tblentry objects for each entry in the table.
        """
        if pos and num:
            # This exists so you can (theoretically) pass altered or unlisted ROMs.
            pass
        elif n64.cmp(region='E', version=0):
            # Pull table offset and size from first instance of it in ASM.
            # Note that num is -1 from actual; last entry is "end of file" mark.
            pos = fetch(n64, NALE[0][1][0])
            num = n64.getASMvalue(NALEnum[0][0], NALEnum[0][1])
        else:
            raise NotImplementedError("{} not yet supported!".format(str(n64)))
        n = pos + (num*12)
        off = int.from_bytes(n64.rom[pos:n], 'big')
        return cls.frombytes(n64.rom[pos:n+off+12], num)

    @classmethod
    def frombytes(cls, data, num):
        from struct import Struct
        pat = Struct(">L4H")
        tbl = cls()
        # Data starts after the table.
        off = num*12 + 12
        s = [pat.unpack_from(data, i) for i in range(0, off, 12)]
        for i in range(num):
            p, t, c, r, d = s[i]
            p&=0x7FFFFFFF
            # Size of entry based on start of next.
            n = s[i+1][0] & 0x7FFFFFFF
            e = n-p
            c<<=2
            if t == 0xFFFF:
                t = None
            else:
                t<<=2
            if r == 0xFFFF:
                r = None
            else:
                r<<=2
            b = data[off+p:off+p+c]
            l = data[off+p+c:off+n] if c<e else b''
            tbl.append(b, l, t, r)
        return tbl

    def tobytes(self):
        """Generates a bytes object for the table and its data."""
        # Have two binaries going: one for entries and another for data.
        tbl = bytearray()
        data= bytearray()
        for i in self:
            t, d = i.tobytes(len(data))
            tbl.extend(t)
            data.extend(d)
            if len(data)&1:
                data.append(0)
        # Add an entry for the endpoint.
        tbl.extend(len(data).to_bytes(4, 'big')+bytes(8))
        tbl.extend(data)
        return bytes(tbl)

class tblentry:
    def __init__(self, data, idx, tbl=None, res=None):
        self.data = data
        self.idx  = idx
        if isinstance(tbl, int) and tbl<0:
            tbl = None
        if isinstance(res, int) and res<0:
            res = None
        self.tbl = tbl
        self.res = res

    def __str__(self):
        if self.data[0:4]==b'vpk0':
            c = "VPK file: 0x{:X} bytes, 0x{:X} uncompressed".format(len(self.data), int.from_bytes(self.data[4:8], 'big'))
        else:
            c = "Binary file: 0x{:X} bytes".format(len(self.data))
        l = ", table of {:d} values".format(len(self.idx)>>1) if self.idx else ""
        t = "" if self.tbl is None else ", filetable @ 0x{:X}".format(self.tbl)
        r = "" if self.res is None else ", resources @ 0x{:X}".format(self.res)
        return "".join((c,l,t,r))

    def tobytes(self, pos=0):
        from struct import Struct
        pat = Struct(">L4H")
        c = (len(self.data)+3)>>2
        if self.data[0:4] == b'vpk0':
            d = (int.from_bytes(self.data[4:8], 'big')+3)>>2
            pos|= 0x80000000
        else:
            d = (len(self.data)+3)>>2
        f = bytes((c<<2) - len(self.data))
        t = 0xFFFF if self.tbl is None else self.tbl>>2
        r = 0xFFFF if self.res is None else self.res>>2
        return (pat.pack(pos, t, c, r, d), self.data+f+self.idx)

    def extract(self, mode='all'):
        """Returns some or all data as a single uncompressed bytearray object.
        Valid modes:
            'all'   decompressed data and idx table combined (default)
            'data'  decompressed data only
            'idx'   idx table only
        """
        out = bytearray()
        if mode in ('all', 'data'):
            if self.data[0:4]==b'vpk0':
                out.extend(VPK.dec_file(self.data))
            else:
                out.extend(self.data)
        if mode in ('all', 'idx'):
            out.extend(self.idx)
        return out

class VPK:
    def _grab(data):
        for i in data:
            yield i

    def _flags(itr):
        """Retrieves next bit from data stream."""
        while True:
            v = next(itr,0)
            for i in range(8):
                yield (v&0x80)>0
                v<<=1

    def _nbits(itr,num):
        """Returns a value equal to the next num bits in stream.
        itr should point to the self._flags() method above."""
        v=0
        for i in range(num):
            v<<=1
            v|=next(itr,0)
        return v

    def header(data):
        """Returns tuple: mode and compressed size"""
        name = data[0:3].decode(errors='replace')
        mode = int(data[3]) - 48
        dec_s= int.from_bytes(data[4:8], byteorder='big')
        if name!="vpk":
            return (-1, dec_s)
        else:
            return (mode, dec_s)

    def _tblbuild(itr):
        """Builds a table of ints and references from a bitsequence.
        itr should be a pointer to _flags(itr) or something similiar."""
        tbl = []
        buf = []
        # Current index and final index, respectively.
        idx = 0
        ## convenience, instead of looking it up all the time
        fin = 0

        while True:
            if next(itr,0):
                # If idx == 0, the table is finished
                if idx<2:
                    break
                ## reference
                tbl.append([buf[idx-2], buf[idx-1], 0])
                buf[idx-2] = fin
                fin+=1
                idx-=1
            else:
                ## integer entry
                v = VPK._nbits(itr, 8)
                tbl.append([0,0,v])
                if len(buf)<=idx:
                    buf.append(fin)
                else:
                    buf[idx] = fin
                fin+=1
                idx+=1
        return tbl

    def _tblsel(itr, tbl, idx=-1):
        """Uses bitflags to iterate the table until non-reference entry found.
        Returns an int with the width given by the table entry.
        itr should be a pointer to _flags(itr) or something similiar."""
        # idx is set to final entry by default; override only if you're doing something special.
        if idx<0:
            idx = len(tbl)-1
        if idx<0:
            return 0

        # Iterates from end fo the list to the beginning; final entry always a reference.
        while not tbl[idx][2]:
            if next(itr,0):
                idx = tbl[idx][1]
            else:
                idx = tbl[idx][0]
        return VPK._nbits(itr, tbl[idx][2])

    def dec0(data, header=None, output=bytearray()):
        """Decompresses vpk0 data to output.
        If header not present, reads it from the file.
        In the case of an error or incompatible format,
            returns an empty bytearray."""
        # initialize the data stream
        if not header:
            header,f = VPK.header(data[0:8])
            if (not f) or (header[0]!=0):
                return output
            d = VPK._grab(data[8:])
        else:
            d = VPK._grab(data)
        f = VPK._flags(d)

        # Retrieve sample length
        sl = VPK._nbits(f,8)

        # Build table 1
        tbl1 = VPK._tblbuild(f)

        # Build table 2
        tbl2 = VPK._tblbuild(f)

        while len(output)<header[1]:
            if next(f,0):
                # Copy bytes from output
                if sl:
                    ## two-sample backtrack lengths
                    l = 0
                    u = VPK._tblsel(f, tbl1)
                    if u<3:
                        l = u+1
                        u = VPK._tblsel(f, tbl1)
                    p = len(output) - (u<<2) - l + 8
                else:
                    ## one-sample backtrack lengths
                    p = len(output) - VPK._tblsel(f, tbl1)
                # Grab #bytes to copy
                n = VPK._tblsel(f, tbl2)
                # Append n bytes from p to output.
                # Do it byte-by-byte to allow replication.
                for i in range(p, p+n):
                    output.append(output[i])
            else:
                # Push next byte to output
                output.append(VPK._nbits(f,8))

        return output

    def dec_file(data):
        """Convenience, reading a header and calling the decompressor."""
        output = bytearray()
        h = VPK.header(data[0:8])
        p = 8

        if h[0]==0:
            output = VPK.dec0(data[p:],h,output)
        return output


class N64():
    def __init__(self, rom):
        """Sets ROM data, unswaps it if swapped,
            and extracts some handy header data.

        Expects rom to be a byte-like object."""
        order = N64.byteorder(rom[0:2])
        if order=='little':
            rom = bytearray(N64.byteswap(rom))
        else:
            self.rom = bytearray(rom)
        self.ID = N64.header(rom[0:64])

    def __str__(self):
        """Returns the ROM's internal name, followed by gameID code and version number if applicable."""
        i = self.ID
        return "{}-{}{}{}{}".format(i.title, i.format, i.name, i.region, '' if not i.version else str(i.version))

    def cmp(self, **kwargs):
        """Compares named attributes in rom ID with kwargs.
        If all attributes match kwargs, return True.
        Typically used to test if the game ID, region, or version match.
        """
        for i in kwargs.keys():
            if getattr(self.ID, i, None)!=kwargs[i]:
                return False
        return True

    def crc(self):
        """Returns CRC in rom as a tuple of integers."""
        u = int.from_bytes(self.rom[16:20], byteorder='big')
        l = int.from_bytes(self.rom[20:24], byteorder='big')
        return (u, l)

    @staticmethod
    def cic_6105(challenge, rsp):
        lut0 = (4, 7, 10, 7, 14, 5, 14, 1, 12, 15, 8, 15, 6, 3, 6, 9)
        lut1 = (4, 1, 10, 7, 14, 5, 14, 1, 12, 9, 8, 5, 6, 3, 12, 9)
        # Copy to working lut.
        lut = lut0
        key = 11
        for i in range(len(challenge)-2):
            rsp[i] = (key + 5 * challenge[i]) & 0xF
            key = lut[rsp[i]]
            sgn = (rsp[i]>>3) & 1
            mag = (~rsp[i] if sgn else rsp[i]) & 7
            mod = sgn if mag%3 == 1 else 1-sgn
            if lut == lut1 and  (rsp[i]==1 or rsp[i]==9):
                mod = 1
            if lut == lut1 and  (rsp[i]==11 or rsp[i]==14):
                mod = 0
            lut = lut1 if mod==1 else lut0


    def calccrc(self, cic='6102', fix=False):
        """Recalculates the CRC based on the CIC chip version given.
        Set fix to True to revise the crc in self.rom."""
        def rol(v, n):
            return (v % 0x100000000)>>n

        cic_names = {
            "6101":0x3F, "starf":0x3F,
            "7102":0x3F, "lylat":0x3F,
            "6102":0x3F, "7101":0x3F, "mario":0x3F,
            "6103":0x78, "7103":0x78, "diddy":0x78,
            "6105":0x91, "7105":0x91, "zelda":0x91,
            "6106":0x85, "7106":0x85, "yoshi":0x85,
            }

        s = cic_names.get(cic)
        if s in (0x78, 0x85):
            seed = 0x6C078965 * s
        else:
            seed = 0x5D588B65 * s
        seed+= 1
        seed&=0xFFFFFFFF
        r1, r2, r3, r4, r5, r6 = seed, seed, seed, seed, seed, seed

        # I wish there was a less horrifying way to do this...
        from array import array
        l = min(0x101000, len(self.rom))
        m = array("L", self.rom[0x1000:l] + bytes(0x101000 - l))
        m.byteswap()
        # Zelda updates the second word a different way...
        if s == 0x91:
            from itertools import cycle
            n = array("L", self.rom[0x750:0x850])
            n.byteswap()
            n = cycle(n)
        # Read each word as an integer.
        for i in m:
            v = (r1+i) & 0xFFFFFFFF
            if v < r1: r2+=1
            v = i & 0x1F
            a = (i<<v) | (rol(i, 0x20-v))
            r1+=i
            r3^=i
            r4+=a
            # You have to limit the result here to 32bits.
            r1&= 0xFFFFFFFF
            r4&= 0xFFFFFFFF
            if r5 < i:
                r5^= (r1^i)
            else:
                r5^=a
            if s == 0x91:
                r6+= (i ^ next(n))
            else:
                r6+= (i ^ r4)
            # Ditto here.
            r5&= 0xFFFFFFFF
            r6&= 0xFFFFFFFF
        # Assemble upper and lower CRCs
        if s == 0x85:
            r1*=r2
            r4*=r5
        else:
##            r2&= 0xFFFFFFFF
            r1^=r2
            r4^=r5
        if s in (0x78, 0x85):
            r1+=r3
            r4+=r6
        else:
            r1^=r3
            r4^=r6
        # Make sure they fit within 4 bytes each.
        r1&= 0xFFFFFFFF
        r4&= 0xFFFFFFFF
        if fix:
            if isinstance(self.rom, bytes):
                self.rom = bytearray(self.rom)
            self.rom[16:20] = r1.to_bytes(4, 'big')
            self.rom[20:24] = r4.to_bytes(4, 'big')
        return (r1,r4)

    @staticmethod
    def byteswap(rom):
        from array import array
        a = array("H", rom)
        a.byteswap()
        return a.tobytes()

    @staticmethod
    def byteorder(rom):
        # Lazy test for byteswapping; let me know about any fringe cases.
        if rom[0]==128:
            return 'big'
        elif rom[1]==128:
            return 'little'
        else:
            raise(ValueError, "Unable to determine byteorder from initial PI settings.")

    @staticmethod
    def header(data):
        """Extracts header data from an unbyteswapped (presumably) N64 ROM.
        IMPORTANT NOTE: currently skipping initialization fields. (ie. PI settings, boot address, clock speed, etc.)

        Returns a namedtuple with the following fields:
            title   cp932 string with encoded filename
            frmt    'N' for carts, 'D' for disk, 'C'/'E' for cart/expansion titles
            name    2-char name; (mostly) unique for each title
            region  char designating intended region
            version integer value, starting at 0, for each release version
        """
        from collections import namedtuple
##        import io
##        if isinstance(data, io.IOBase):
##            data = data.read(64)

        title = data[32:52].decode(encoding='cp932',errors='replace').strip()
        # get ID
        frmt=data[59:60].decode(errors="replace")
        ID  =data[60:62].decode(errors="replace")
        reg =data[62:63].decode(errors="replace")
        ver =data[63]
        clck=int.from_bytes(data[4:8],  byteorder='big') & ~0xF
        pc  =int.from_bytes(data[8:12], byteorder='big')
        rls =int.from_bytes(data[12:16],byteorder='big')
        nt  =namedtuple('ID', ['initPI', 'clock', 'PC', 'release', 'title', 'frmt', 'name', 'region', 'version'])
        pi  =namedtuple('PI', ['release', 'pagesize', 'pulsewidth', 'latency'])
        p = pi(data[1]>>4, data[1]&0xF, data[2], data[3])
        return nt(p, clck, pc, rls, title, frmt, ID, reg, ver)

    def getASMvalue(self, upper, lower=None):
        if lower is None:
            u = int.from_bytes(self.rom[upper+2:upper+4], 'big') & 0x3FFFFFF
            u<<=2
            return u | 0x80000000
        u = int.from_bytes(self.rom[upper+2:upper+4], 'big')<<16
        e = False if self.rom[lower]&0x10 else True
        l = int.from_bytes(self.rom[lower+2:lower+4], 'big', signed=e)
        return u+l

    def setASMvalue(self, value, upper, lower=None):
        if lower is None:
            value>>=2
            value&=0x3FFFFFF
            u = self.rom[upper] & 0xFC
            value|= u<<24
            self.rom[upper:upper+4] = value.to_bytes(4, 'big')
        else:
            u = value>>16
            l = value&0xFFFF
            if not self.rom[lower]&0x10:
                u += bool(l&0x8000)
            self.rom[upper+2:upper+4] = u.to_bytes(2, 'big')
            self.rom[lower+2:lower+4] = l.to_bytes(2, 'big')


class FileChoice():
    """
    A modal dialog for selecting files to be imported.
    """
    def __init__(self, master=None, title="Select Files", message="Select one or more files to inject.", basename="", entry=None):
        if not entry:
            raise ValueError("An entry must be provided!")
        self.master = master
        self.data= entry.data
        self.idx = entry.idx
        self.res = entry.res
        self.tbl = entry.tbl
        # These are working replacement values.
        self.newdata= entry.data
        self.newidx = entry.idx
        self.newres = StringVar()
        self.newtbl = StringVar()

        self.modalPane = Toplevel(self.master)
        self.modalPane.focus_set()
        self.modalPane.transient(self.master)
        self.modalPane.wait_visibility()
        self.modalPane.grab_set()
        #self.master.wait_window(self.modalPane)
        self.modalPane.resizable(True, False)

        self.modalPane.rowconfigure(1, weight=1)
        self.modalPane.columnconfigure(1, weight=1)
        self.modalPane.minsize(150,50)

##        self.modalPane.bind("<Return>", self._choose)
        self.modalPane.bind("<Escape>", self._cancel)

        if title:
            self.modalPane.title(title)

        if message:
            Label(self.modalPane, text=message).grid(row=0, column=0, padx=1, pady=1)

        from tkinter.ttk import Labelframe
        from tkinter.tix import Control
        # Data file browser
        dataFrame= Labelframe(self.modalPane, text="Data")
        dataFrame.grid(row=1, column=0, sticky='W')
        self.dataPresent = IntVar()
        self.dataPresent.set(bool(self.newdata))
        opt_data = Checkbutton(dataFrame, state=DISABLED, variable=self.dataPresent, command=(lambda: self._toggle_available(target=opt_data_load, var=self.dataPresent)))
        opt_data_load = Button(dataFrame, text="Browse...", state=NORMAL if self.dataPresent.get() else DISABLED, command=(lambda:self._browse(field='newdata', title="Load Data File:", exts=bin_exts)))
        opt_data.grid(row=0, column=0)
        opt_data_load.grid(row=0, column=1)

        # Idx file browser
        idxFrame= Labelframe(self.modalPane, text="Idx Table")
        idxFrame.grid(row=2, column=0, sticky='W')
        self.idxPresent = IntVar()
        self.idxPresent.set(bool(self.newidx))
        opt_idx = Checkbutton(idxFrame, variable=self.idxPresent, command=(lambda: self._toggle_available(target=opt_idx_load, var=self.idxPresent)))
        opt_idx_load = Button(idxFrame, text="Browse...", state=NORMAL if self.idxPresent.get() else DISABLED, command=(lambda:self._browse(field='newidx', title="Load Idx Table:", exts=req_exts)))
        opt_idx.grid(row=0, column=0)
        opt_idx_load.grid(row=0, column=1)

        # res offset input
        resFrame= Labelframe(self.modalPane, text="Resource Offset")
        resFrame.grid(row=3, column=0, sticky='W')
        self.resPresent = IntVar()
        self.res_except = StringVar()
        self.resPresent.set(bool(self.res is not None))
        opt_res = Checkbutton(resFrame, variable=self.resPresent, command=(lambda: self._toggle_available(target=opt_res_ctrl, var=self.resPresent)))
        opt_res_ctrl = Entry(resFrame, state=NORMAL if self.resPresent.get() else DISABLED, textvariable=self.newres, exportselection=False)
        opt_res_except=Label(resFrame, textvariable=self.res_except, fg='red')
        self.newres.set(hex(entry.res if entry.res else 0))
        opt_res.grid(row=0, column=0)
        opt_res_ctrl.grid(row=0, column=1)
        opt_res_except.grid(row=0, column=2)

        # tbl offset input
        tblFrame= Labelframe(self.modalPane, text="Filetable Offset")
        tblFrame.grid(row=4, column=0, sticky='W')
        self.tblPresent = IntVar()
        self.tbl_except = StringVar()
        self.tblPresent.set(bool(self.tbl is not None))
        opt_tbl = Checkbutton(tblFrame, variable=self.tblPresent, command=(lambda: self._toggle_available(target=opt_tbl_ctrl, var=self.tblPresent)))
        opt_tbl_ctrl = Entry(tblFrame, state=NORMAL if self.tblPresent.get() else DISABLED, textvariable=self.newtbl, exportselection=False)
        opt_tbl_except=Label(tblFrame, textvariable=self.tbl_except, fg='red')
        self.newtbl.set(hex(entry.tbl if entry.tbl else 0))
        opt_tbl.grid(row=0, column=0)
        opt_tbl_ctrl.grid(row=0, column=1)
        opt_tbl_except.grid(row=0, column=2)

        # Accept/cancel buttons.
        buttonFrame = Frame(self.modalPane)
        buttonFrame.grid(row=5, column=0, padx=1, pady=1)
        # This button loads files and evaluates values.
        chooseButton = Button(buttonFrame, text="Done", command=self._choose, default=ACTIVE)
        chooseButton.grid(in_=buttonFrame, row=0, column=0, padx=5, pady=1, sticky=E)

        cancelButton = Button(buttonFrame, text="Cancel", command=self._cancel)
        cancelButton.grid(in_=buttonFrame, row=0, column=1, padx=5, pady=1, sticky=W)
        self.modalPane.wait_window(self.modalPane)

    def _toggle_available(self, event=None, target=None, var=None):
        target["state"]=NORMAL if var.get() else DISABLED

    def _browse(self, event=None, field=None, title="", exts=()):
        if not field:
            return
        exts += def_exts
        f = filedialog.askopenfile(mode='rb', title=title,filetypes=exts, defaultextension=exts[0][1].strip('*'))
        if f:
            setattr(self, field, f.read())
            f.close()

    def _choose(self, event=None):
        err = False
        # If tbl set, convert value; else return None
        if not self.tblPresent.get():
            tbl = None
        else:
            try:
                tbl = int(self.newtbl.get(), base=0)
                if tbl<0 or tbl>len(self.newdata):
                    raise ValueError("Offset must be a positive number within the data file.")
                self.tbl_except.set("")
            except Exception as E:
                self.tbl_except.set(str(E))
                err=True
        # If res set, convert value; else return None
        if not self.resPresent.get():
            res = None
        else:
            try:
                res = int(self.newres.get(), base=0)
                if res<0 or res>len(self.newdata):
                    raise ValueError("Offset must be a positive number within the data file.")
                self.res_except.set("")
            except Exception as E:
                self.res_except.set(str(E))
                err=True
        # On any error, update the dialog with red ink and return
        if err:
            return
        # Otherwise, update self values with new ones and close.
        self.data= self.newdata if self.dataPresent.get() else b""
        self.idx = self.newidx  if self.idxPresent.get()  else b""
        self.tbl = tbl
        self.res = res
        self._cancel()

    def _cancel(self, event=None):
        self.modalPane.destroy()

    def returnValue(self):
        """Return value is a tuple of all fields."""
        return (self.data, self.idx, self.res, self.tbl)


class Application(Frame):
    """Super Smash Bros. File Inserter v1.0
    Currently only replaces files within the filetable, correcting all subsequent file offsets.

    Usage:
        *) Use "File/Open ROM" to load a Super Smash Bros. ROM.
           A list of files, their types, and special offsets will appear.
        *) Double left-click an entry in the list to bring up a context menu.
           "Edit" allows you to load files or change offset values.
           "Export File(s)" decompresses data and appends the idx table if present, saving them as a single file.
           "Export Data" decompresses data and saves it to a file.
           "Export Idx Table" only extracts and saves the idx table.
           "Export Raw" copies both blocks of data from the ROM as-is.
        *) The Edit dialog is still a bit crude.
           The checkboxes to the left of each field disable it.  Use this to remove an offset or table.
           Use "Browse..." to replace the entry's data or idx table with a new one.
             An idx table should never be compressed.
             Data may be vpk compressed, but there is no native support to do so yet.  You'll have to use an external tool for now.
           The offsets are a physical offset in <data> to certain tables.
             If you want to give a hexadecimal address preceed the number with "0x".
           Press "Done" to update the entry, or "Cancel" to undo any changes.
        *) Use "File/Save New ROM" to save a new ROM with any changes.
           The output ROM will be unbyteswapped with a corrected CRC.
           By default the ROM is padded to the nearest 4MB boundry with 0xFFs.
             This can be disabled using "Options/Pad Output ROMs".
    """

    def testAltered(self):
        """Tests all files within self.rom against their uneditted hash values.
        Any altered files will append their index to self.altered.
        """
        if not self.rom:
            return
##
##        h = int.from_bytes(self.rom.rom[0x48DA:0x48DC], byteorder='big') << 16
##        l = int.from_bytes(self.rom.rom[0x48DE:0x48E0], byteorder='big', signed=True)
##        filetable = h + l
##        h = int.from_bytes(self.rom.rom[0x4922:0x4924], byteorder='big') << 16
##        l = int.from_bytes(self.rom.rom[0x4926:0x4928], byteorder='big', signed=True)
##        base = h + l
##
##        from array import array
##        import hashlib
##
##        # Build up a table of addresses
##        tbl = array("L", self.rom.rom[filetable:filetable+0xCC8C])
##        tbl.byteswap()
##        for i in range(len(tbl)):
##            tbl[i]&= ~1
##            tbl[i]+= base
##        # Slice each file from self.rom and pass through ConfirmEdit()
##        # If True, add that index to the set.
##        print("Testing files against hash table.\nPlease wait...")
##        l = len(tbl)//10
##        s = set()
##        for i in range(len(tbl)-1):
##            if not i % l:
##                print("\t{:6>d} of {:d}".format(i, len(tbl)))
##            h = hashlib.md5(self.rom.rom[tbl[i]:tbl[i+1]]).digest()
##            if h != bytes.fromhex(fileattr[i][1]):
##                s.add(i)
##        print("Done.")
##        self.altered = s
##        self._fillListbox()

    def changeROM(self, Event=None, new_rom=None):
        """Open ROM using filedialog, and on success close any existing ROM."""
        if not new_rom:
            new_rom = filedialog.askopenfilename(title="Open Super Smash Bros. ROM File",filetypes=rom_exts + def_exts, defaultextension=rom_exts[0][1].strip('*'))
        try:
            f = open(new_rom, 'rb')
        except IOError as E:
            # print the error
            if new_rom: print(E)
        else:
            print("Reading and verifying ROM.")
            rom = N64(f.read())
            f.close()
            # read in header
            if not rom.cmp(frmt= 'N', name='AL'):
                print("The file does not use SSB's game ID.")
            else:
                self.rom = rom
                self.altered = set()
                self.status_rom.set(new_rom)
                self.entries = SSBtbl.fromROM(rom)
                print("File loaded successfully.")
                self._fillListbox()
        # Magically toggle options.
        i = NORMAL if self.rom else DISABLED
        for j in self.edit_ready:
            j[0](j[1] ,state=i)
        self.update()

    def changeDir(self, Event=None, new_dir=None):
        """Change output directory. (well, working directory)"""
        if not new_dir:
            new_dir = filedialog.askdirectory(title="Change Working Directory")
        if new_dir:
            import os
            os.chdir(new_dir)
            self.status_dir.set(': '.join(("Current Dir.", os.getcwd())))
            app.update()

    def compress(self, Event=None):
        """Compress and save a single stand-alone file."""
        print("Not Implemented")
##        f = filedialog.askopenfilename(filetypes=def_exts)
##        if f:
##            b = f.read()
##            f.close()
##            if b:
##                lz = LZSS.encode(b, fill=0)
##                f_out = filedialog.asksaveasfile(mode="wb",title="Save LZ File As:", filetypes=(("Compressed (LZ77) Files", '*.lz'),) + def_exts, defaultextension='.lz')
##                if f_out:
##                    f_out.write(lz)
##                    f_out.close()

    def saveROM(self, Event=None):
        if not self.rom:
            return
        if self.rom.cmp(region='E', version=0):
            tbl = NALE
            ntbl= NALEnum
        else:
            raise NotImplementedError("{} not yet supported!".format(str(n64)))
        # Apply any patches.
        # Update the filetable; align to QW boundry as per standard.
        data = self.entries.tobytes()
        o = fetch(self.rom, tbl[0][1][0])
        n = fetch(self.rom, ntbl[0])
        s = (len(data)+o)&15
        if s:
            b = b'\xb6U#\x950\xec+\x8d\xb6U#\x950\xec+\x8d'
            data += b[s:]
        # Replace the original table.  If number of entries changed, do that too.
        ReplaceData(self.rom, "filetable.bin", data)
        if n!=len(self.entries):
            v = len(self.entries) - n
            for i in ntbl:
                process(self.rom, i, v)
        # Correct the checksum, add any desired padding, then write.
        FinishROM(self.rom, pad=self.pad.get())
        self.saveAs(data=self.rom, exts=rom_exts)

    def saveAs(self, Event=None, new_rom=None, data=None, exts=tuple()):
        """
        Generic Save-File-As dialog.
        Tries to generate the directory and filename specified,
            and worst case asks you for one.
        Saves data to file and closes it.
        Returns True if anything goes wrong, False otherwise
        """
        # You should be outputting something.
        if not data:
            return
        if isinstance(data, N64):
            data = data.rom

        f_out = None
        exts += def_exts
        if new_rom:
            import os
            try:
                # attempt to generate the directory if it doesn't exist.
                d = os.path.dirname(new_rom)
                if not os.path.exists(d):
                    os.makedirs(d)
                f_out = open(new_rom, 'wb')
            except:
                f_out = filedialog.asksaveasfile(mode="wb",title="Save {} As:".format(new_rom), initialfile="{}".format(new_rom), filetypes=exts, defaultextension=exts[0][1].strip('*'))
        else:
            f_out = filedialog.asksaveasfile(mode="wb",title="Save File As:", filetypes=exts, defaultextension=exts[0][1].strip('*'))
        if not f_out:
            return
        try:
            print("Writing data to {}.".format(f_out.name))
            f_out.write(data)
            f_out.close()
            print("File saved.")
            return False
        except:
            print("Failed to write file.")
            return True

    def inject(self, Event=None, slot=None):
        if slot is None:
            return
        # FileChoice recieves and returns the entry's data.
        update = FileChoice(basename="resource_{:04d}".format(slot), entry=self.entries[slot]).returnValue()
        if not update:
            return
        self.entries[slot].data= update[0]
        self.entries[slot].idx = update[1]
        self.entries[slot].res = update[2]
        self.entries[slot].tbl = update[3]
        # Update the display.
        self._fillListbox()

    def importList(self, Event=None):
        n = filedialog.askopenfilename(title="Open File List", filetypes=csv_exts + def_exts, defaultextension=csv_exts[0][1].strip('*'))
        if n:
            r = filelist(self.rom, n)
            if r:
                self.rom.rom = r

    ## These three generate the window itself.
    def createWidgets(self):
        """Place widgets here."""
        import importlib
        #Allow resizing
        top = self.winfo_toplevel()
        top.minsize(119,130)
        top.resizable(True, True)
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        self.rowconfigure(1, minsize=119, weight=1)
        self.columnconfigure(0, weight=1)

        # Format for entries: index, hex(offset), type, name
##        self.entries = tuple("{: <8d}0x{:X}\t{}\t{}".format(c, i[0], i[3], i[2]) for c,i in enumerate(fileattr))
##        self.listbox = ListBoxWindow(self, entries=l)
        scrollBar = Scrollbar(self)
        scrollBar.grid(row=1, column=1, sticky=N+S)
        self.listBox = Listbox(self, selectmode=SINGLE)
        self.listBox.grid(row=1, column=0, sticky=(N,S,E,W))
        scrollBar.config(command=self.listBox.yview)
        self.listBox.config(yscrollcommand=scrollBar.set, activestyle=None)
        listMenu = Menu(self.listBox, tearoff=False)
        listMenu.add_command(label="Edit", command=(lambda: self._choose(mode='edit')))
##        listMenu.add_command(label='Set Filetable Offset', command=(lambda: self._choose(mode='tbl')))
##        listMenu.add_command(label='Set Resource Offset', command=(lambda: self._choose(mode='res')))
        listMenu.add_separator()
        listMenu.add_command(label="Export File(s)", command=(lambda: self._choose(mode='all')))
        listMenu.add_command(label="Export Data", command=(lambda: self._choose(mode='data')))
        listMenu.add_command(label="Export Idx Table", command=(lambda: self._choose(mode='idx')))
        listMenu.add_command(label="Export Raw", command=(lambda: self._choose(mode='raw')))
        self.edit_ready.append((listMenu.entryconfigure, 0))
        self.edit_ready.append((listMenu.entryconfigure, 2))
        self.edit_ready.append((listMenu.entryconfigure, 3))
        self.edit_ready.append((listMenu.entryconfigure, 4))
        self.edit_ready.append((listMenu.entryconfigure, 5))
##        self.edit_ready.append((listMenu.entryconfigure, 6))
##        self.edit_ready.append((listMenu.entryconfigure, 7))
        listMenu.add_separator()
        listMenu.add_command(label='Cancel')
        self.listBox.bind("<Double-Button-1>", lambda e: listMenu.post(e.x_root, e.y_root))
        ## self._fillListbox()

        # status bar
        import os
        self.status_dir.set(': '.join(("Cur. Directory", os.getcwd())))
        status_r = Label(self, textvariable=self.status_dir)
        status_r.grid(row=0, column=0, columnspan=2, sticky=NW)
        self.status_rom.set('No File Loaded')
        status_l = Label(self, textvariable=self.status_rom)
        status_l.grid(row=3, column=0, columnspan=2, sticky=SW)

        from tkinter.ttk import Sizegrip
        Sizegrip(self).grid(row=3, column=1, sticky=(S,E))

    def createMenubar(self):
        import importlib
        """Generates menubar and child cascade menus."""
        # Menubar
        top = self.winfo_toplevel()
        top.option_add('*tearOff', False)
        menubar = Menu(top)
        top['menu'] = menubar
        self.menus = menubar
        # set up the accelerator names
        keys = "Cmd-" if (root.tk.call('tk', 'windowingsystem')=='aqua') else "Ctrl+"
        # File menu...
        mb_file = Menu(menubar, name='file')
        menubar.add_cascade(label="File", menu=mb_file, underline=0)
        mb_file.add_command(label="Open ROM", command=self.changeROM, accelerator=(keys+'O'))
        mb_file.add_command(label="Change Directory", command=self.changeDir, accelerator=(keys+'D'))
        mb_file.add_command(label="Save New ROM", command=self.saveROM, accelerator=(keys+'S'))
        self.edit_ready.append((mb_file.entryconfigure, 2))
        mb_file.add_separator()
        mb_file.add_command(label="Exit", command=self.quit)
        # Import menu...
        mb_imp  = Menu(menubar, name='import')
        menubar.add_cascade(label="Import", menu=mb_imp, underline=0)
        self.edit_ready.append((menubar.entryconfigure, 1))
        mb_imp.add_command(label="Run Build Script", command=self.importList, state=DISABLED)
##        mb_imp.add_separator()
##        mb_imp.add_command(label="Insert Global Text", command=self.setGlobal)
        # Tools menu...
        mb_tool = Menu(menubar, name='tools')
        menubar.add_cascade(label="Tools", menu=mb_tool, underline=0)
        mb_tool.add_command(label="Detect Altered Files", command=self.testAltered, state=DISABLED)
##        self.edit_ready.append((mb_tool.entryconfigure, 1))
        # Output options...
        mb_opt  = Menu(menubar, name='options', tearoff=True)
        menubar.add_cascade(label="Options", menu=mb_opt, underline=0)
        mb_opt.add_checkbutton(label="Pad Output ROMs", variable=self.pad)
        # Help menu...
        mb_help = Menu(menubar, name='help')
        menubar.add_cascade(label="Help", menu=mb_help, underline=0)
        mb_help.add_command(label="How to Use", command=(lambda: print(self.__doc__)))
        mb_help.add_command(label="About", state=DISABLED)

    def accelerators(self):
        """Sets up hotkeys."""
        # dumb Mac and its CoMmAnD key...
        if (root.tk.call('tk', 'windowingsystem')=='aqua'):
            keys, accel = "Command", "Cmd-"
        else:
            keys, accel = "Control", "Ctrl+"
        # File menu options; iterates over menu to pull them out in a gratuitously complex way ;*)
        # TODO: generalize the children part so you can pass children of children.
        for i in self.menus.children:
            e = self.menus.children[i]
            for j in range(e.index(END)):
                if e.type(j) == 'command':
                    c = e.entrycget(j, 'accelerator')
                    if c and e.entrycget(j, 'state')!=DISABLED:
                        # This is the id() of the Tcl command usually passed by the invoke() method.
                        func = e._tclCommands[j]
                        if accel in c:
                            a = c.replace(accel, '')
                            # This adds upper and lower case entries for single letter accelerators.
                            # This ensures Ctrl+C and Ctrl+c both share the same function.
                            if len(a)==1:
                                self.master.bind("<{}-{}>".format(keys, a.upper()), lambda event, f=func: self.tk.call(f))
                                a = a.lower()
                            # Longer keybindings, like Ctrl+Del, only go here.
                            self.master.bind("<{}-{}>".format(keys, a), lambda event, f=func: self.tk.call(f))
                        else:
                            # For everything else, there's this.
                            self.master.bind("<{}>".format(a), lambda event, f=func: self.tk.call(f))

    def _fillListbox(self, event=None):
        """Called to initialize the entries in the listbox.
        Call anytime the base ROM has changed to ensure your list is accurate."""
        # populate the list
        self.listBox.delete(0,END)
        for c, item in enumerate(self.entries):
            self.listBox.insert(END, "{:04d}: {}".format(c,item))
        # colorize every other line
        for c in range(len(self.entries)):
            if c&1:
                hc = '#f0f0ff' if c not in self.altered else '#d0f0d0'
                self.listBox.itemconfigure(c, background=hc)
            elif c in self.altered:
                self.listBox.itemconfigure(c, background='#dfffd0')

    def _choose(self, event=None, mode=None):
        if not self.rom or not mode:
            return
        try:
            Index = [i for i in map(int,self.listBox.curselection())]
            if mode=='edit':
                for x in Index:
                    self.inject(slot=x)
            elif mode=='tbl':
                print("Not Implemented")
            elif mode=='res':
                print("Not Implemented")
            elif mode in ('all', 'data'):
                for x in Index:
                    self.saveAs(new_rom="resource_{:04d}.bin".format(x), data=self.entries[x].extract(mode), exts=bin_exts)
            elif mode=='idx':
                for x in Index:
                    if self.entries[x].idx:
                        self.saveAs(new_rom="resource_{:04d}.req".format(x), data=self.entries[x].idx, exts=req_exts)
            elif mode=='raw':
                for x in Index:
                    self.saveAs(new_rom="resource_{:04d}.bin".format(x), data=self.entries[x].data, exts=bin_exts)
                    if self.entries[x].idx:
                        self.saveAs(new_rom="resource_{:04d}.req".format(x), data=self.entries[x].idx, exts=req_exts)
        except IndexError:
            pass

    def __init__(self, master=None):
        # General values
        self.rom = None
        self.altered = []
        self.edit_ready = []
        self.pad = IntVar()
        self.pad.set(True)
        # TK initialization
        Frame.__init__(self, master)
        self.master.title("Super Smash Bros. File Inserter")
        self.grid_configure(padx=3, pady=3, ipadx=3, ipady=3, sticky=NSEW)
        # TK variables
        self.status_dir = StringVar()
        self.status_rom = StringVar()
        # build window, menubar, and map hotkeys
        self.createWidgets()
        self.createMenubar()
        self.accelerators()
        # Disable the rom-reliant stuff.
        for i in self.edit_ready:
            i[0](i[1], state=DISABLED)

def drawgui():
    global app
    global root

    ## Initialize the UI and Go!
    root = Tk()
    app = Application(master=root)

    app.mainloop()
    try:    root.destroy()
    except: pass


def cmdfile(args, p):
    n = N64(args.rom.read())
    args.rom.close()
    # Make sure some yahoo didn't pass some crazy file.
    if not n.cmp(frmt= 'N', name='AL'):
        p.error("Did not detect an NAL* ROM file.")

    n.rom = filelist(n, args.filelist)
    n.rom = FinishROM(n.rom, pad=args.pad)
    with open(args.out, 'wb') as f:
        f.write(n.rom)
    p.exit(0, 'Finished.  Your output is in {}.'.format(args.out))


def cmdline(args, p):
    n = N64(args.rom.read())
    args.rom.close()
    # Make sure some yahoo didn't pass some crazy file.
    if not n.cmp(frmt= 'N', name='AL'):
        p.error("Did not detect an NAL* ROM file.")

    # Read data, and if it's a csv file convert it.
    new = args.data.read()
    args.data.close()

    i = int(args.slot, base=0)
    if i not in fileidx:
        p.error("Unable to find index 0x{:X} in original filelist.".format(i))
    out = N64(offset(n.rom, fileidx.index(i), new))
    # Fix the checksum if necessary.
    out.calccrc(fix=True)
    # Find EOF and pad if requested.
    out.rom = FinishROM(out.rom, pad=args.extend)

    with open(args.out, 'wb') as f:
        f.write(out.rom)
    p.exit(0, 'Finished.  Your output is in {}.'.format(args.out))


def main():
    drawgui()

    #TODO: port command line to SSB
##    import argparse
##
##    parser = argparse.ArgumentParser(description="Inserts file in VPW2 ROM", fromfile_prefix_chars='@')
##    sub = parser.add_subparsers(dest='subparser_name')
##    # Note: in order to catch hex prefixes on ints they're being returned as strings.
##    # These are used when opening as a window.
##    # These are used in 'insert' mode, as a command line without GUI.
##    sub_cmd = sub.add_parser('insert')
##    sub_cmd.set_defaults(func=cmdline)
##    sub_cmd.add_argument('rom', type=argparse.FileType('rb'))
##    sub_cmd.add_argument('slot', type=str, help='Index in the filetable, as an integer.')
##    sub_cmd.add_argument('data', type=argparse.FileType('rb'))
##    sub_cmd.add_argument('out', nargs='?', type=str, default="output.n64")
##    sub_cmd.add_argument('-e', '--extend', action='store_true', default=False, help='Set to pad output to next 4MB boundry.')
##    # These allow you to use a filelist on the command line.
##    # TODO: have them both use the same function, since filelist is pretty much identical.
##    sub_lst = sub.add_parser('list')
##    sub_lst.set_defaults(func=cmdfile)
##    sub_lst.add_argument('rom', type=argparse.FileType('rb'))
##    sub_lst.add_argument('filelist', type=str)
##    sub_lst.add_argument('out', nargs='?', type=str, default="output.n64")
##    sub_lst.add_argument('-e', '--extend', action='store_true', default=False, help='Set to pad output to next 4MB boundry.')
##
##    a = parser.parse_args()
##    if not a.subparser_name:
##        drawgui()
##    else:
##        a.func(a, parser)


if __name__ == '__main__':
    main()

