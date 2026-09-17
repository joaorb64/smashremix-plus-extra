"""
Wires up bio-overflow loading in CharacterDataScreen.asm on top of Remix's
baseline setup_files/extend_biography_file scopes, and later fills in
however many overflow files a build actually needed
"""
import lineinfile

from smashremix_extra.asm_util import add_to_scope

_SETUP_FILES_ENTRIES = [
    "",
    "        // Bio overflow files load in a loop via Render.load_file_",
    "        // (proven-safe helper, saves/restores ra), storing each pointer",
    "        // into bio_overflow_pointers. bio_overflow_ids/bio_overflow_pointers",
    "        // are filled in at build time (character_appender.py) with however",
    "        // many files this build actually needed - loop stops at the first",
    "        // zero id, no fixed cap.",
    "",
    "        // j+delay-slot is always 2 words, but only `lui s0, 0x8000` here is",
    "        // meant to be intercepted. The next word (`lui a1, 0x8013`, setting",
    "        // up the following call's argument) gets clobbered too - both get",
    "        // replayed below or that next call crashes with a garbage a1.",
    "        OS.patch_start(0x15FF0C, 0x80133EBC)",
    "        j       _load_bio_overflow_files",
    "        lui     s0, 0x8000              // original instr @ 0x80133EBC (1st clobbered word)",
    "        _return_from_load:",
    "        OS.patch_end()",
    "",
    "        _load_bio_overflow_files:",
    "        addiu   sp, sp, -0x0020",
    "        sw      ra, 0x0018(sp)",
    "",
    "        li      t3, bio_overflow_ids",
    "        li      t4, bio_overflow_pointers",
    "        _load_loop:",
    "        lw      a0, 0x0000(t3)",
    "        beqz    a0, _load_loop_done",
    "        nop",
    "        sw      t3, 0x0010(sp)           // t3/t4 are caller-saved - jal below",
    "        sw      t4, 0x0014(sp)           // can (and does) clobber them",
    "        or      a1, t4, r0",
    "        jal     Render.load_file_",
    "        nop",
    "        lw      t3, 0x0010(sp)",
    "        lw      t4, 0x0014(sp)",
    "        addiu   t3, t3, 4",
    "        j       _load_loop",
    "        addiu   t4, t4, 4",
    "        _load_loop_done:",
    "",
    "        lw      ra, 0x0018(sp)",
    "        addiu   sp, sp, 0x0020",
    "",
    "        j       _return_from_load",
    "        lui     a1, 0x8013               // original instr @ 0x80133EC0 (2nd clobbered word), replayed",
    "",
    "        bio_overflow_ids:",
    "        dw 0x00000000     // terminator, loop stops at the first zero id",
    "",
    "        bio_overflow_pointers:",
]

_BIOGRAPHY_ANCHOR = r"^\s*OS\.read_word\(EXTENDED_BIOS_POINTER, t1\).*$"

_BIOGRAPHY_LOOKUP = """\
        // lb sign-extends, so byte 0x80 reads back as 0xFFFFFF80 - addiu
        // sign-extends its immediate the same way, so this compare is safe.
        addiu   at, r0, 0xFF80
        beq     t9, at, _file1           // 0x80 -> 10F5.bin (original extended file)
        nop

        // Anything else (0x81, 0x82, ...) indexes into
        // setup_files.bio_overflow_pointers (index = flag_byte - 0x81).
        addiu   t2, t9, 0x007F
        sll     t2, t2, 0x0002
        li      t0, setup_files.bio_overflow_pointers
        addu    t0, t0, t2
        lw      t1, 0x0000(t0)           // t1 = this overflow file's loaded pointer
        j       _return + 0x04
        lw      t9, 0x0020(sp)          // t9 = offset (og line 3)

        _file1:
        OS.read_word(EXTENDED_BIOS_POINTER, t1) // t1 = remix file pointer"""


def apply_bio_overflow_patches():
    add_to_scope(
        "src/CharacterDataScreen.asm", "scope setup_files",
        _SETUP_FILES_ENTRIES)
    lineinfile.add_line_to_file(
        filepath="src/CharacterDataScreen.asm",
        line=_BIOGRAPHY_LOOKUP,
        regexp=_BIOGRAPHY_ANCHOR,
    )


def write_bio_overflow_asm(names):
    if not names:
        return
    lineinfile.add_line_to_file(
        filepath="src/CharacterDataScreen.asm",
        line="\n".join(f"        dw File.{name}" for name in names),
        inserter=lineinfile.AfterLast(r"^\s*bio_overflow_ids:\s*$")
    )
    lineinfile.add_line_to_file(
        filepath="src/CharacterDataScreen.asm",
        line="\n".join("        dw 0x00000000" for _ in names),
        inserter=lineinfile.AfterLast(r"^\s*bio_overflow_pointers:\s*$")
    )
