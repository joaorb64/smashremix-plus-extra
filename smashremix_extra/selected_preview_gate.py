"""Generate the selected-only VS CSS fighter-preview gate."""

import re

PREVIEW_POLICY_AUTO = 0
PREVIEW_POLICY_FORCE_ON = 1
PREVIEW_POLICY_FORCE_OFF = 2
PREVIEW_POLICY = PREVIEW_POLICY_AUTO
PREVIEW_PROBE_WAIT_EACH_IO = True

MARKER = "// +EXTRA selected-only VS CSS previews v3"
TITLE_DIAGNOSTIC_MARKER = "// +EXTRA SummerCart raw identifier diagnostic v2"
_WAIT_EACH_IO_TOKEN = "__PREVIEW_PROBE_WAIT_EACH_IO__"
_WAIT_UNLOCK_1_TOKEN = "__PREVIEW_PROBE_WAIT_UNLOCK_1__"
_WAIT_UNLOCK_2_TOKEN = "__PREVIEW_PROBE_WAIT_UNLOCK_2__"
_WAIT_IDENTIFIER_TOKEN = "__PREVIEW_PROBE_WAIT_IDENTIFIER__"

_BOOT_VERSION_DRAW = "        Render.draw_string(1, 3, string_version, Render.NOOP, 0x43200000, 0x435A0000, 0x888800FF, 0x3F700000, Render.alignment.CENTER)"
_BOOT_VERSION_PATTERN = re.compile(
    r'^(?P<indent>\s*)string_version:; String\.insert\("(?P<version>[^"\r\n]*)"\)\s*$',
    re.MULTILINE,
)
_BOOT_DIAGNOSTIC_DRAW = f"""        {TITLE_DIAGNOSTIC_MARKER}
        jal     CharacterSelect.resolve_preview_workaround_
        nop
        li      t0, CharacterSelect.css_preview_probe_identifier
        lw      t0, 0x0000(t0)
        li      t1, string_probe_identifier + 4
        lli     t2, 0x0008
        _format_probe_identifier:
        srl     t3, t0, 0x001C
        sltiu   t4, t3, 0x000A
        bnez    t4, _store_probe_digit
        addiu   t5, t3, 0x0030
        addiu   t5, t3, 0x0037
        _store_probe_digit:
        sb      t5, 0x0000(t1)
        sll     t0, t0, 0x0004
        addiu   t1, t1, 0x0001
        addiu   t2, t2, -0x0001
        bnez    t2, _format_probe_identifier
        nop
{_BOOT_VERSION_DRAW}
        Render.draw_string(1, 3, string_probe_identifier, Render.NOOP, 0x43200000, 0x43660000, 0x888800FF, 0x3F700000, Render.alignment.CENTER)"""
_BOOT_PROBE_STRING = 'string_probe_identifier:; String.insert("[ID:00000000]")'


def _pi_wait_block(label: str) -> str:
    return f"""        lui     t1, 0xA460
        {label}:
        lw      t2, 0x0010(t1)
        andi    t2, t2, 0x0003
        bnez    t2, {label}
        nop
"""

_SYNC_PRISTINE = """    scope sync_slot_used_by_port: {
        li      t0, dynamic_css.slot_used_by_port
        lw      t1, 0x0004(t0)              // curr_slot_used_by_port
        jr      ra
        sw      t1, 0x0000(t0)              // update slot_used_by_port
    }
"""
_SYNC_CANONICAL = """    scope sync_slot_used_by_port: {
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0030
        sw      ra, 0x002C(sp)
        sw      t2, 0x0028(sp)
        sw      t3, 0x0024(sp)
        sw      v0, 0x0020(sp)
        jal     resolve_preview_workaround_
        nop
        beqz    v0, _stock_sync
        nop
        li      t2, css_preview_dispatch_state
        lw      t3, 0x0000(t2)
        bnez    t3, _nested_return        // nested dispatch has no clock or lifecycle authority
        nop
        lli     t3, CSS_PREVIEW_DISPATCHING
        sw      t3, 0x0000(t2)
        li      t2, css_preview_frame_serial
        lw      t3, 0x0000(t2)
        addiu   t3, t3, 0x0001
        sw      t3, 0x0000(t2)
        li      t0, dynamic_css.slot_used_by_port
        lw      t1, 0x0004(t0)              // curr_slot_used_by_port
        sw      t1, 0x0000(t0)              // update slot_used_by_port
        jal     css_preview_frame_
        nop
        li      t2, css_preview_dispatch_state
        sw      r0, 0x0000(t2)
        b       _nested_return
        nop
        _stock_sync:
        li      t0, dynamic_css.slot_used_by_port
        lw      t1, 0x0004(t0)              // curr_slot_used_by_port
        sw      t1, 0x0000(t0)              // preserve stock slot synchronization
        _nested_return:
        lw      v0, 0x0020(sp)
        lw      t3, 0x0024(sp)
        lw      t2, 0x0028(sp)
        lw      ra, 0x002C(sp)
        jr      ra
        addiu   sp, sp, 0x0030
    }
"""

_ANCHOR = "    constant CSS_PLAYER_STRUCT(0x8013BA88)\n"
_TRAINING_ANCHOR = "    constant CSS_PLAYER_STRUCT_TRAINING(0x80138558)\n"
_SELECTED_MARKER = re.compile(r"^\s*// \+EXTRA selected-only VS CSS previews\b.*$", re.MULTILINE)
_LEGACY_MARKER = re.compile(
    r"^\s*// \+EXTRA (?:"
    r"no-preview|"
    r"safe multiplayer no-preview|"
    r"multiplayer fighter-preview suppression|"
    r"committed preview replay spike|"
    r"preview teardown gate spike|"
    r"exact serialized CSS preview scheduler|"
    r"preview lifecycle|"
    r"preview debounce"
    r")\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

_STOCK_INDICATOR_PRISTINE = """        _draw_indicator:
        li      t1, Character.id.NONE
        beq     t1, s3, _next               // skip drawing if no character displayed
        nop
        addiu   sp, sp,-0x0020              // allocate stack space
"""
_STOCK_INDICATOR_CANONICAL = """        _draw_indicator:
        li      t1, Character.id.NONE
        beq     t1, s3, _next               // skip drawing if no character displayed
        nop
        jal     resolve_preview_workaround_
        nop
        beqz    v0, _draw_stock_indicator   // disabled policy retains stock hover indicators
        nop
        lw      t1, 0x0088(s2)              // t1 = character selected state
        beqz    t1, _next                    // selected-only previews have no hover fighter object
        nop
        _draw_stock_indicator:
        addiu   sp, sp,-0x0020              // allocate stack space
"""

_BLOCK = f"""

    {MARKER}
    constant CSS_PREVIEW_POLICY_AUTO({PREVIEW_POLICY_AUTO})
    constant CSS_PREVIEW_POLICY_FORCE_ON({PREVIEW_POLICY_FORCE_ON})
    constant CSS_PREVIEW_POLICY_FORCE_OFF({PREVIEW_POLICY_FORCE_OFF})
    constant CSS_PREVIEW_POLICY({PREVIEW_POLICY})
    constant CSS_PREVIEW_WAIT_EACH_IO({_WAIT_EACH_IO_TOKEN})
    constant CSS_PREVIEW_WORKAROUND_UNKNOWN(0xFFFFFFFF)
    constant CSS_PREVIEW_WORKAROUND_DISABLED(0)
    constant CSS_PREVIEW_WORKAROUND_ENABLED(1)
    constant CSS_PREVIEW_DEBOUNCE_FRAMES(18)
    constant CSS_PREVIEW_SUPPRESSED(0)
    constant CSS_PREVIEW_WAITING(1)
    constant CSS_PREVIEW_CONSTRUCTING(2)
    constant CSS_PREVIEW_VISIBLE(3)
    constant CSS_PREVIEW_REVOKE_PENDING(4)
    constant CSS_PREVIEW_DISPATCHING(1)
    constant CSS_PREVIEW_ACTION_NONE(0)
    constant CSS_PREVIEW_ACTION_REVOKE(1)
    constant FORCE_SELECTED_PREVIEW_OWNER_INACTIVE(0xFFFFFFFF)
    constant CSS_PREVIEW_OWNER_INACTIVE(0xFFFFFFFF)
    css_preview_workaround_enabled:
    dw CSS_PREVIEW_WORKAROUND_UNKNOWN
    css_preview_probe_identifier:
    dw CSS_PREVIEW_WORKAROUND_UNKNOWN
    forced_selected_preview_owner:
    dw FORCE_SELECTED_PREVIEW_OWNER_INACTIVE
    // One serialized preview record, shared by P1/P2/P3/P4 while the fixed prefix is open.
    css_preview_owner:; dw CSS_PREVIEW_OWNER_INACTIVE
    css_preview_policy_owner:; dw CSS_PREVIEW_OWNER_INACTIVE
    css_preview_policy_generation:; dw 0
    css_preview_request_generation:; dw 0
    css_preview_dispatch_state:; dw 0
    css_preview_action:; dw CSS_PREVIEW_ACTION_NONE
    css_preview_frame_serial:; dw 0
    css_preview_reclaim_cursor:; dw 0
    css_preview_slot_epochs:
    dw 0, 0, 0, 0, 0
    css_preview_retirement_character:
    dw 0, 0, 0, 0, 0
    css_preview_retirement_epoch:
    dw 0, 0, 0, 0, 0
    css_preview_retirement_frame:
    dw 0, 0, 0, 0, 0
    css_preview_retirement_valid:
    dw 0, 0, 0, 0, 0
    css_preview_pending_character:; dw Character.id.NONE
    css_preview_pending_variant:; dw 0
    css_preview_frame_countdown:; dw 0
    css_preview_state:; dw CSS_PREVIEW_SUPPRESSED
    p1_preview_suppress_depth:; dw 0

    OS.patch_start(0x134458, 0x801361D8)
    jal     selected_preview_make_gate_
    or      a3, v0, r0                  // original delay slot
    OS.patch_end()

    OS.patch_start(0x135474, 0x801371F4)
    jal     selected_preview_on_select_
    sw      v1, 0x0018(sp)              // original delay slot
    OS.patch_end()

    // Resolve once. AUTO enables only for the documented SummerCart64 "SCv2" identifier.
    scope resolve_preview_workaround_: {{
        li      t0, css_preview_workaround_enabled
        lw      v0, 0x0000(t0)
        li      t1, CSS_PREVIEW_WORKAROUND_UNKNOWN
        bne     v0, t1, _return
        nop

        lli     t1, CSS_PREVIEW_POLICY_FORCE_ON
        lli     t2, CSS_PREVIEW_POLICY
        beq     t2, t1, _enable
        nop
        lli     t1, CSS_PREVIEW_POLICY_FORCE_OFF
        beq     t2, t1, _disable
        nop

        // Match libcart's direct-I/O safety rule before touching cartridge registers.
        lui     t1, 0xA460
        _wait_for_pi:
        lw      t2, 0x0010(t1)
        andi    t2, t2, 0x0003           // PI_STATUS_DMA_BUSY | PI_STATUS_IO_BUSY
        bnez    t2, _wait_for_pi
        nop

        // SC64 registers are locked after cold boot/NMI. KEY always accepts this sequence.
        lui     t1, 0xBFFF
        sw      r0, 0x0010(t1)
{_WAIT_UNLOCK_1_TOKEN}
        lui     t1, 0xBFFF
        lui     t2, 0x5F55
        ori     t2, t2, 0x4E4C
        sw      t2, 0x0010(t1)
{_WAIT_UNLOCK_2_TOKEN}
        lui     t1, 0xBFFF
        lui     t2, 0x4F43
        ori     t2, t2, 0x4B5F
        sw      t2, 0x0010(t1)
{_WAIT_IDENTIFIER_TOKEN}
        lui     t1, 0xBFFF
        lw      t2, 0x000C(t1)
        li      t3, css_preview_probe_identifier
        sw      t2, 0x0000(t3)
        lui     t3, 0x5343
        ori     t3, t3, 0x7632
        beq     t2, t3, _enable
        nop

        _disable:
        lli     v0, CSS_PREVIEW_WORKAROUND_DISABLED
        b       _cache
        nop
        _enable:
        lli     v0, CSS_PREVIEW_WORKAROUND_ENABLED
        _cache:
        sw      v0, 0x0000(t0)
        _return:
        jr      ra
        nop
    }}

    // Pure fixed-prefix eligibility: P1..P4 must stay open in order.
    scope ordered_preview_owner_: {{
        li      t0, CSS_PLAYER_STRUCT

        lw      t1, 0x0084(t0)          // P1 MAN/CPU state
        sltiu   t2, t1, 0x0002
        beqz    t2, _inactive
        nop
        lw      t1, 0x0058(t0)          // P1 selected
        beqz    t1, _owner_p1
        nop

        lw      t1, 0x0140(t0)          // P2 MAN/CPU state (0xBC stride)
        sltiu   t2, t1, 0x0002
        beqz    t2, _inactive
        nop
        lw      t1, 0x0114(t0)          // P2 selected
        beqz    t1, _owner_p2
        nop

        lw      t1, 0x01FC(t0)          // P3 MAN/CPU state (two strides)
        sltiu   t2, t1, 0x0002
        beqz    t2, _inactive
        nop
        lw      t1, 0x01D0(t0)          // P3 selected
        beqz    t1, _owner_p3
        nop

        lw      t1, 0x02B8(t0)          // P4 MAN/CPU state (three strides)
        sltiu   t2, t1, 0x0002
        beqz    t2, _inactive
        nop
        lw      t1, 0x028C(t0)          // P4 selected
        beqz    t1, _owner_p4
        nop

        _inactive:
        li      v1, CSS_PREVIEW_OWNER_INACTIVE
        jr      ra
        nop
        _owner_p1:
        li      v1, 0
        jr      ra
        nop
        _owner_p2:
        li      v1, 1
        jr      ra
        nop
        _owner_p3:
        li      v1, 2
        jr      ra
        nop
        _owner_p4:
        li      v1, 3
        jr      ra
        nop
    }}

    // Update the one global policy epoch; invalidation only clears metadata, never a fighter object.
    scope refresh_preview_policy_: {{
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0020
        sw      ra, 0x001C(sp)
        jal     ordered_preview_owner_
        nop
        li      t0, css_preview_policy_owner
        lw      t1, 0x0000(t0)
        beq     v1, t1, _return
        nop
        sw      v1, 0x0000(t0)
        li      t0, css_preview_policy_generation
        lw      t1, 0x0000(t0)
        addiu   t1, t1, 0x0001
        sw      t1, 0x0000(t0)
        li      t0, css_preview_pending_character
        lw      t1, 0x000C(t0)
        lli     t2, CSS_PREVIEW_VISIBLE
        beq     t1, t2, _return           // frame owns visible policy-loss teardown
        nop
        li      t0, css_preview_owner
        li      t2, CSS_PREVIEW_OWNER_INACTIVE
        sw      t2, 0x0000(t0)
        li      t0, css_preview_pending_character
        lli     t2, Character.id.NONE
        sw      t2, 0x0000(t0)
        sw      r0, 0x0004(t0)
        sw      r0, 0x0008(t0)
        sw      r0, 0x000C(t0)
        li      t0, css_preview_request_generation
        sw      r0, 0x0000(t0)
        _return:
        lw      ra, 0x001C(sp)
        jr      ra
        addiu   sp, sp, 0x0020
    }}

    scope selected_preview_on_select_: {{
        // Preserve the patched call's inputs while resolving the runtime policy.
        addiu   sp, sp, -0x0030
        sw      ra, 0x002C(sp)
        sw      a0, 0x0028(sp)
        sw      a1, 0x0024(sp)
        sw      a2, 0x0020(sp)
        sw      a3, 0x001C(sp)
        jal     resolve_preview_workaround_
        nop
        or      t3, v0, r0
        lw      a3, 0x001C(sp)
        lw      a2, 0x0020(sp)
        lw      a1, 0x0024(sp)
        lw      a0, 0x0028(sp)
        lw      ra, 0x002C(sp)
        addiu   sp, sp, 0x0030
        beqz    t3, _stock_select
        nop

        sltiu   t3, a0, 0x0004           // stock puck/player index is unsigned 0..3
        bnez    t3, _valid_puck
        nop
        j       0x80131C74                // invalid input: retain stock selection behavior
        nop
        _valid_puck:
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0040
        sw      ra, 0x003C(sp)
        sw      a0, 0x0038(sp)
        sw      a1, 0x0034(sp)
        sw      a2, 0x0030(sp)
        sw      a3, 0x002C(sp)

        li      t0, CSS_PLAYER_STRUCT
        lli     t1, 0x00BC
        multu   a0, t1
        mflo    t1
        addu    t0, t0, t1
        lw      t0, 0x0080(t0)          // held player index
        sw      t0, 0x0028(sp)

        sltiu   t3, t0, 0x0004           // do not index CSS panels with a corrupt held index
        beqz    t3, _invalid_held_player
        nop

        bnez    t0, _keep_p1_scheduler
        nop
        li      t2, css_preview_owner
        li      t1, CSS_PREVIEW_OWNER_INACTIVE
        sw      t1, 0x0000(t2)
        li      t2, css_preview_pending_character
        lli     t1, Character.id.NONE
        sw      t1, 0x0000(t2)
        sw      r0, 0x0004(t2)
        sw      r0, 0x0008(t2)
        sw      r0, 0x000C(t2)
        li      t2, css_preview_request_generation
        sw      r0, 0x0000(t2)
        _keep_p1_scheduler:

        li      t2, forced_selected_preview_owner
        lw      t1, 0x0000(t2)
        sw      t1, 0x0024(sp)           // preserve outer forced owner for nested stock updates
        or      a0, t0, r0
        sw      t0, 0x0000(t2)           // exact held player owns this forced stock update
        jal     0x80136128              // stock mnPlayersVSUpdateFighter
        nop
        lw      a0, 0x0028(sp)          // exact held player after native successful construction
        jal     record_dynamic_slot_binding_
        nop
        li      t2, forced_selected_preview_owner
        lw      t1, 0x0024(sp)
        sw      t1, 0x0000(t2)           // balanced restoration preserves an outer owner

        lw      a0, 0x0038(sp)
        lw      a1, 0x0034(sp)
        lw      a2, 0x0030(sp)
        lw      a3, 0x002C(sp)
        jal     0x80131C74              // stock mnPlayersVSSelectFighterPuck
        nop
        sw      v0, 0x0020(sp)           // preserve native selection results across optional cleanup
        sw      v1, 0x001C(sp)
        jal     refresh_preview_policy_   // selection can advance P1->P2, P2->P3, or P3->P4
        nop

        lw      t0, 0x0028(sp)          // held player index
        li      t1, CSS_PLAYER_STRUCT
        lli     t2, 0x00BC
        multu   t0, t2
        mflo    t2
        addu    t1, t1, t2
        lw      t2, 0x0058(t1)          // panel is_selected after stock selection
        bnez    t2, _return
        nop
        bnez    t0, _hide_denied_preview
        nop
        li      t2, p1_preview_suppress_depth
        lli     t1, 0x0001
        lw      t3, 0x0000(t2)
        addu    t3, t3, t1
        sw      t3, 0x0000(t2)           // stock update synchronously re-enters this gate
        _hide_denied_preview:
        or      a0, t0, r0
        jal     0x80136128              // unforced update hides a denied C-button preview
        nop
        bnez    t0, _return
        nop
        li      t2, p1_preview_suppress_depth
        lw      t3, 0x0000(t2)
        addiu   t3, t3, -0x0001
        sw      t3, 0x0000(t2)

        _return:
        lw      v0, 0x0020(sp)
        lw      v1, 0x001C(sp)
        lw      ra, 0x003C(sp)
        addiu   sp, sp, 0x0040
        jr      ra
        nop

        _invalid_held_player:
        lw      a0, 0x0038(sp)
        lw      a1, 0x0034(sp)
        lw      a2, 0x0030(sp)
        lw      a3, 0x002C(sp)
        jal     0x80131C74                // invalid held index: do not force or cancel scheduler
        nop
        sw      v0, 0x0020(sp)
        sw      v1, 0x001C(sp)
        b       _return
        nop

        _stock_select:
        j       0x80131C74                // disabled policy retains stock selection behavior
        nop
    }}

    scope selected_preview_make_gate_: {{
        // Preserve the native construction hook inputs while resolving the runtime policy.
        addiu   sp, sp, -0x0020
        sw      ra, 0x001C(sp)
        sw      a0, 0x0018(sp)
        sw      a1, 0x0014(sp)
        sw      v0, 0x0010(sp)
        jal     resolve_preview_workaround_
        nop
        or      t5, v0, r0
        lw      v0, 0x0010(sp)
        lw      a1, 0x0014(sp)
        lw      a0, 0x0018(sp)
        lw      ra, 0x001C(sp)
        addiu   sp, sp, 0x0020
        beqz    t5, _allow
        nop

        sltiu   t5, a1, 0x0004           // native player index is unsigned 0..3
        beqz    t5, _return
        nop
        li      t0, CSS_PLAYER_STRUCT
        lli     t1, 0x00BC
        multu   a1, t1
        mflo    t1
        addu    t0, t0, t1
        // The policy is refreshed for every native gate call, including P1.
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0040
        sw      ra, 0x003C(sp)
        sw      a0, 0x0038(sp)
        sw      a1, 0x0034(sp)
        sw      v0, 0x0030(sp)
        sw      t0, 0x002C(sp)
        jal     refresh_preview_policy_
        nop
        lw      t0, 0x002C(sp)
        lw      v0, 0x0030(sp)
        lw      a1, 0x0034(sp)
        lw      a0, 0x0038(sp)
        lw      ra, 0x003C(sp)
        addiu   sp, sp, 0x0040
        lw      t2, 0x0058(t0)          // panel is_selected
        bnez    a1, _non_p1
        nop
        b       _p1
        nop
        _non_p1:
        bnez    t2, _allow
        nop
        li      t3, forced_selected_preview_owner
        lw      t3, 0x0000(t3)
        beq     a1, t3, _allow           // forced stock selection may bypass this gate
        nop
        sltiu   t5, v1, 0x0004           // P1/P2/P3/P4 can own the one global record.
        beqz    t5, _hide_p1_hover
        nop
        bne     a1, v1, _hide_p1_hover
        nop
        lw      t3, 0x0084(t0)
        sltiu   t4, t3, 0x0002
        beqz    t4, _hide_p1_hover
        nop
        lw      t3, 0x0048(t0)
        sltiu   t4, t3, Character.NUM_CHARACTERS
        beqz    t4, _hide_p1_hover
        nop
        lli     t4, Character.id.PLACEHOLDER
        beq     t3, t4, _hide_p1_hover
        nop
        lli     t4, Character.id.NONE
        beq     t3, t4, _hide_p1_hover
        nop
        b       _schedule
        nop

        _return:
        jr      ra
        nop

        _p1:
        bnez    t2, _cancel_and_allow
        nop
        li      t3, forced_selected_preview_owner
        lw      t3, 0x0000(t3)
        beq     a1, t3, _cancel_and_allow // forced P1 selection cancels its scheduler and allows
        nop
        lw      t3, 0x0084(t0)          // MAN/CPU are the only open panel states
        sltiu   t4, t3, 0x0002
        beqz    t4, _cancel_p1
        nop
        li      t2, p1_preview_suppress_depth
        lw      t3, 0x0000(t2)
        bnez    t3, _cancel_p1
        nop
        lw      t3, 0x0048(t0)          // exact displayed character request
        sltiu   t4, t3, Character.NUM_CHARACTERS
        beqz    t4, _cancel_p1
        nop
        lli     t4, Character.id.PLACEHOLDER
        beq     t3, t4, _cancel_p1
        nop
        lli     t4, Character.id.NONE
        beq     t3, t4, _cancel_p1
        nop
        _schedule:
        li      t2, css_preview_pending_character
        li      t5, css_preview_owner
        lw      t4, 0x0000(t5)
        bne     a1, t4, _restart
        nop
        lw      t4, 0x0000(t2)
        bne     t3, t4, _restart
        nop
        lw      t4, 0x0004(t2)
        bne     v0, t4, _restart         // hook-time resolved variant is part of the request
        nop
        li      t5, css_preview_policy_generation
        lw      t4, 0x0000(t5)
        li      t5, css_preview_request_generation
        lw      t5, 0x0000(t5)
        bne     t4, t5, _restart          // every debounced request is bound to one policy epoch
        nop
        lw      t4, 0x000C(t2)
        lli     t5, CSS_PREVIEW_CONSTRUCTING
        beq     t4, t5, _allow            // nested exact loader construction releases only itself
        nop
        lli     t5, CSS_PREVIEW_VISIBLE
        beq     t4, t5, _allow
        nop
        // Exact WAITING requests are denied without advancing time; render owns the clock.
        b       _hide_p1_hover
        nop

        _restart:
        li      t5, css_preview_owner
        sw      a1, 0x0000(t5)
        sw      t3, 0x0000(t2)
        sw      v0, 0x0004(t2)
        lli     t4, CSS_PREVIEW_DEBOUNCE_FRAMES
        sw      t4, 0x0008(t2)
        lli     t4, CSS_PREVIEW_WAITING
        sw      t4, 0x000C(t2)
        li      t5, css_preview_policy_generation
        lw      t4, 0x0000(t5)
        li      t5, css_preview_request_generation
        sw      t4, 0x0000(t5)
        b       _hide_p1_hover
        nop

        _hide_p1_hover:
        beqz    a0, _return
        nop
        lli     t4, 0x0001
        sw      t4, 0x007C(a0)           // only P1 scheduler denials hide retained previews
        b       _return
        nop

        _cancel_and_allow:
        li      t5, css_preview_owner
        li      t4, CSS_PREVIEW_OWNER_INACTIVE
        sw      t4, 0x0000(t5)
        li      t2, css_preview_pending_character
        lli     t4, Character.id.NONE
        sw      t4, 0x0000(t2)
        sw      r0, 0x0004(t2)
        sw      r0, 0x0008(t2)
        sw      r0, 0x000C(t2)
        li      t5, css_preview_request_generation
        sw      r0, 0x0000(t5)
        b       _allow
        nop

        _cancel_p1:
        li      t5, css_preview_owner
        li      t4, CSS_PREVIEW_OWNER_INACTIVE
        sw      t4, 0x0000(t5)
        li      t2, css_preview_pending_character
        lli     t4, Character.id.NONE
        sw      t4, 0x0000(t2)
        sw      r0, 0x0004(t2)
        sw      r0, 0x0008(t2)
        sw      r0, 0x000C(t2)
        li      t5, css_preview_request_generation
        sw      r0, 0x0000(t5)
        beqz    a0, _return
        nop
        lli     t4, 0x0001
        sw      t4, 0x007C(a0)           // only P1 cancellation denies this construction
        b       _return
        nop

        _allow:
        j       0x80134A8C              // native mnPlayersVSMakeFighter entry
        nop
    }}

    // Render callback: frame clock for stationary P1/P2/P3/P4 hover debounce.
    scope css_preview_frame_: {{
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0080
        sw      ra, 0x007C(sp)
        sw      at, 0x0048(sp)
        sw      a0, 0x0044(sp)
        sw      a1, 0x0040(sp)
        sw      a2, 0x003C(sp)
        sw      a3, 0x0038(sp)
        sw      v0, 0x0034(sp)
        sw      v1, 0x0030(sp)
        sw      t2, 0x002C(sp)
        sw      t3, 0x0028(sp)
        sw      t4, 0x0024(sp)
        sw      t5, 0x0020(sp)
        sw      t6, 0x001C(sp)
        sw      t7, 0x0018(sp)
        sw      t8, 0x0014(sp)
        sw      t9, 0x0010(sp)

        jal     reclaim_retired_slot_
        nop
        bnez    v0, _return                // reset/stale abandonment owns this callback
        nop

        jal     refresh_preview_policy_   // prerequisite changes invalidate metadata before ticking
        nop

        li      t2, css_preview_pending_character
        lw      t3, 0x000C(t2)
        lli     t4, CSS_PREVIEW_VISIBLE
        beq     t3, t4, _visible
        nop
        lli     t4, CSS_PREVIEW_WAITING
        bne     t3, t4, _return
        nop
        b       _waiting
        nop

        _visible:
        li      t4, css_preview_owner
        lw      t5, 0x0000(t4)
        sltiu   t6, t5, 0x0004
        beqz    t6, _clear
        nop
        li      t6, css_preview_policy_owner
        lw      t6, 0x0000(t6)
        beq     t5, t6, _return           // owner remains eligible
        nop
        li      t4, CSS_PLAYER_STRUCT
        lli     t6, 0x00BC
        multu   t5, t6
        mflo    t6
        addu    t4, t4, t6
        lw      t6, 0x0058(t4)
        bnez    t6, _return                // selected later fighters are never revoked
        nop
        lw      t6, 0x0088(t4)
        bnez    t6, _return                // explicit selected state is also required
        nop
        lw      t6, 0x0008(t4)
        beqz    t6, _clear                 // inconsistent visible-null has no allocator ownership
        nop
        // Capture all teardown inputs before the destructor mutates anything.
        sw      t4, 0x0050(sp)             // panel
        sw      t6, 0x0054(sp)             // fighter GObj
        sw      t5, 0x0068(sp)             // exact owner; never reload mutable global after jal
        lw      t7, 0x0048(t4)
        sw      t7, 0x0058(sp)             // retained hover ID
        li      t7, dynamic_css.curr_slot_used_by_port
        addu    t7, t7, t5
        lb      t7, 0x0000(t7)
        sw      t7, 0x005C(sp)             // signed current dynamic slot
        sltiu   t8, t7, dynamic_css.ACTIVE_HEAP_COUNT
        beqz    t8, _capture_frame
        nop
        sll     t8, t7, 0x0002
        li      t9, css_preview_slot_epochs
        addu    t9, t9, t8
        lw      t9, 0x0000(t9)
        sw      t9, 0x0060(sp)
        _capture_frame:
        li      t9, css_preview_frame_serial
        lw      t9, 0x0000(t9)
        sw      t9, 0x0064(sp)
        lli     t8, CSS_PREVIEW_REVOKE_PENDING
        sw      t8, 0x000C(t2)
        li      t8, css_preview_action
        lli     t9, CSS_PREVIEW_ACTION_REVOKE
        sw      t9, 0x0000(t8)
        lw      a0, 0x0054(sp)
        jal     0x800D78E8
        nop
        li      t2, css_preview_pending_character // external destructor may clobber caller-saved t2
        lw      t4, 0x0050(sp)
        lw      t5, 0x005C(sp)
        sw      r0, 0x0008(t4)
        lw      t6, 0x0068(sp)             // captured teardown owner
        li      t4, dynamic_css.curr_slot_used_by_port
        addu    t4, t4, t6
        lli     t6, -1
        sb      t6, 0x0000(t4)
        sltiu   t6, t5, dynamic_css.ACTIVE_HEAP_COUNT
        beqz    t6, _clear
        nop
        sll     t5, t5, 0x0002
        li      t4, css_preview_retirement_character
        addu    t4, t4, t5
        lw      t6, 0x0058(sp)
        sw      t6, 0x0000(t4)
        li      t4, css_preview_retirement_epoch
        addu    t4, t4, t5
        lw      t6, 0x0060(sp)
        sw      t6, 0x0000(t4)
        li      t4, css_preview_retirement_frame
        addu    t4, t4, t5
        lw      t6, 0x0064(sp)
        sw      t6, 0x0000(t4)
        li      t4, css_preview_retirement_valid
        addu    t4, t4, t5
        lli     t6, 0x0001
        sw      t6, 0x0000(t4)          // valid last
        b       _clear
        nop

        _waiting:
        li      t4, css_preview_owner
        lw      t5, 0x0000(t4)
        sltiu   t6, t5, 0x0004
        beqz    t6, _clear
        nop
        li      t6, css_preview_policy_owner
        lw      t6, 0x0000(t6)
        bne     t5, t6, _clear            // request owner must remain the current policy owner
        nop
        li      t6, css_preview_policy_generation
        lw      t6, 0x0000(t6)
        li      t7, css_preview_request_generation
        lw      t7, 0x0000(t7)
        bne     t6, t7, _clear            // request epoch must remain the current policy epoch
        nop
        li      t4, CSS_PLAYER_STRUCT
        lli     t6, 0x00BC
        multu   t5, t6
        mflo    t6
        addu    t4, t4, t6
        lw      t5, 0x0058(t4)
        bnez    t5, _clear
        nop
        lw      t5, 0x0084(t4)
        sltiu   t5, t5, 0x0002
        beqz    t5, _clear
        nop
        lw      t5, 0x0048(t4)
        lw      t6, 0x0000(t2)
        bne     t5, t6, _clear
        nop
        lw      t5, 0x004C(t4)          // live panel variant compared with hook-time resolved v0
        lw      t6, 0x0004(t2)
        bne     t5, t6, _clear
        nop
        lw      t5, 0x0008(t2)
        beqz    t5, _clear                // malformed WAITING zero must clear, never underflow
        nop
        addiu   t5, t5, -0x0001
        sw      t5, 0x0008(t2)
        bnez    t5, _return
        nop
        lli     t5, CSS_PREVIEW_CONSTRUCTING
        sw      t5, 0x000C(t2)
        li      t6, css_preview_owner
        lw      t6, 0x0000(t6)
        jal     0x80136128              // proven native per-port loader
        or      a0, t6, r0               // serialized record owner, safe single-instruction delay slot

        li      t2, css_preview_pending_character
        lw      t3, 0x000C(t2)
        lli     t4, CSS_PREVIEW_CONSTRUCTING
        bne     t3, t4, _return          // nested mismatch preserved newest request
        nop
        li      t4, css_preview_owner
        lw      t5, 0x0000(t4)
        sltiu   t6, t5, 0x0004
        beqz    t6, _clear
        nop
        li      t6, css_preview_policy_owner
        lw      t6, 0x0000(t6)
        bne     t5, t6, _clear            // request owner must remain the current policy owner
        nop
        li      t6, css_preview_policy_generation
        lw      t6, 0x0000(t6)
        li      t7, css_preview_request_generation
        lw      t7, 0x0000(t7)
        bne     t6, t7, _clear            // request epoch must remain the current policy epoch
        nop
        li      t4, CSS_PLAYER_STRUCT
        lli     t6, 0x00BC
        multu   t5, t6
        mflo    t6
        addu    t4, t4, t6
        lw      t5, 0x0058(t4)
        bnez    t5, _clear
        nop
        lw      t5, 0x0084(t4)
        sltiu   t5, t5, 0x0002
        beqz    t5, _clear
        nop
        lw      t5, 0x0048(t4)
        lw      t6, 0x0000(t2)
        bne     t5, t6, _clear
        nop
        lw      t5, 0x004C(t4)          // live panel variant compared with hook-time resolved v0
        lw      t6, 0x0004(t2)
        bne     t5, t6, _clear
        nop
        lw      t5, 0x0008(t4)          // native load must publish this owner's fighter object
        beqz    t5, _clear
        nop
        li      a0, css_preview_owner
        lw      a0, 0x0000(a0)
        jal     record_dynamic_slot_binding_
        nop
        _publish_visible:
        lli     t5, CSS_PREVIEW_VISIBLE
        sw      t5, 0x000C(t2)
        b       _return
        nop

        _clear:
        li      t4, css_preview_owner
        li      t3, CSS_PREVIEW_OWNER_INACTIVE
        sw      t3, 0x0000(t4)
        lli     t3, Character.id.NONE
        sw      t3, 0x0000(t2)
        sw      r0, 0x0004(t2)
        sw      r0, 0x0008(t2)
        sw      r0, 0x000C(t2)
        li      t4, css_preview_request_generation
        sw      r0, 0x0000(t4)
        li      t4, css_preview_action
        sw      r0, 0x0000(t4)
        _return:
        lw      t9, 0x0010(sp)
        lw      t8, 0x0014(sp)
        lw      t7, 0x0018(sp)
        lw      t6, 0x001C(sp)
        lw      t5, 0x0020(sp)
        lw      t4, 0x0024(sp)
        lw      t3, 0x0028(sp)
        lw      t2, 0x002C(sp)
        lw      v1, 0x0030(sp)
        lw      v0, 0x0034(sp)
        lw      a3, 0x0038(sp)
        lw      a2, 0x003C(sp)
        lw      a1, 0x0040(sp)
        lw      a0, 0x0044(sp)
        lw      at, 0x0048(sp)
        lw      ra, 0x007C(sp)
        jr      ra
        addiu   sp, sp, 0x0080
    }}

    // The only epoch writer: successful native construction binds this panel to an active slot.
    scope record_dynamic_slot_binding_: {{
        sltiu   t0, a0, 0x0004
        beqz    t0, _return
        nop
        li      t0, CSS_PLAYER_STRUCT
        lli     t1, 0x00BC
        multu   a0, t1
        mflo    t1
        addu    t0, t0, t1
        lw      t1, 0x0008(t0)
        beqz    t1, _return                // native path did not publish a fighter object
        nop
        li      t0, dynamic_css.curr_slot_used_by_port
        addu    t0, t0, a0
        lb      t1, 0x0000(t0)
        sltiu   t0, t1, dynamic_css.ACTIVE_HEAP_COUNT
        beqz    t0, _return                // signed -1/preloaded and corrupt slots have no epoch
        nop
        sll     t1, t1, 0x0002
        li      t0, css_preview_slot_epochs
        addu    t0, t0, t1
        lw      t1, 0x0000(t0)
        addiu   t1, t1, 0x0001
        sw      t1, 0x0000(t0)
        _return:
        jr      ra
        nop
    }}

    // Inspect exactly one retired dynamic slot per outer callback.  v0=1 consumes it.
    scope reclaim_retired_slot_: {{
        // o32 outgoing argument home area: sp+0x00..0x0C is callee-owned.
        addiu   sp, sp, -0x0030
        sw      ra, 0x002C(sp)
        sw      a0, 0x0028(sp)
        sw      t0, 0x0024(sp)
        sw      t1, 0x0020(sp)
        li      t0, css_preview_reclaim_cursor
        lw      a0, 0x0000(t0)
        sltiu   t1, a0, dynamic_css.ACTIVE_HEAP_COUNT
        bnez    t1, _cursor_valid
        nop
        or      a0, r0, r0                 // corrupted cursor clamps before any slot-array access
        _cursor_valid:
        sw      a0, 0x001C(sp)
        addiu   t1, a0, 0x0001
        sltiu   t2, t1, dynamic_css.ACTIVE_HEAP_COUNT
        bnez    t2, _store_cursor
        nop
        or      t1, r0, r0
        _store_cursor:
        sw      t1, 0x0000(t0)
        sll     t1, a0, 0x0002
        li      t0, css_preview_retirement_valid
        addu    t0, t0, t1
        lw      t2, 0x0000(t0)
        beqz    t2, _normal
        nop
        sw      t0, 0x0018(sp)            // validity address/token; clear before reset
        li      t0, css_preview_retirement_frame
        addu    t0, t0, t1
        lw      t2, 0x0000(t0)
        li      t3, css_preview_frame_serial
        lw      t3, 0x0000(t3)
        subu    t4, t3, t2                  // modular age = current - retirement
        beqz    t4, _normal                 // same frame remains pending
        nop
        lui     t5, 0x8000
        sltu    t4, t4, t5
        beqz    t4, _normal                 // future/half-range-or-older remains pending
        nop
        li      t0, dynamic_css.slot_used_by_port
        lbu     t2, 0x0000(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0001(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0002(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0003(t0)
        beq     t2, a0, _normal
        nop
        li      t0, dynamic_css.curr_slot_used_by_port
        lbu     t2, 0x0000(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0001(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0002(t0)
        beq     t2, a0, _normal
        nop
        lbu     t2, 0x0003(t0)
        beq     t2, a0, _normal
        nop
        sll     t1, a0, 0x0002
        li      t0, css_preview_retirement_epoch
        addu    t0, t0, t1
        lw      t2, 0x0000(t0)
        li      t0, css_preview_slot_epochs
        addu    t0, t0, t1
        lw      t3, 0x0000(t0)
        bne     t2, t3, _abandon
        nop
        li      t0, css_preview_retirement_character
        addu    t0, t0, t1
        lw      t2, 0x0000(t0)
        sll     t1, a0, 0x0004
        li      t0, dynamic_css.heap_slot_0
        addu    t0, t0, t1
        lw      t3, 0x0004(t0)
        beq     t2, t3, _reset
        nop
        lbu     t3, 0x0008(t0)
        beq     t2, t3, _reset
        nop
        lbu     t3, 0x0009(t0)
        beq     t2, t3, _reset
        nop
        lbu     t3, 0x000A(t0)
        beq     t2, t3, _reset
        nop
        lbu     t3, 0x000B(t0)
        beq     t2, t3, _reset
        nop
        _abandon:
        lw      t0, 0x0018(sp)
        sw      r0, 0x0000(t0)
        lli     v0, 0x0001
        b       _return
        nop
        _reset:
        lw      t0, 0x0018(sp)
        sw      r0, 0x0000(t0)             // nested reset cannot duplicate or erase newer data
        lw      a0, 0x001C(sp)
        jal     reset_heap_slot_
        nop
        lli     v0, 0x0001
        b       _return
        nop
        _normal:
        or      v0, r0, r0
        _return:
        lw      t1, 0x0020(sp)
        lw      t0, 0x0024(sp)
        lw      a0, 0x0028(sp)
        lw      ra, 0x002C(sp)
        jr      ra
        addiu   sp, sp, 0x0030
    }}
"""


def transform_character_select(source: str) -> str:
    """Insert the gate only into the exact pristine CSS transform region."""
    if PREVIEW_POLICY not in {
        PREVIEW_POLICY_AUTO,
        PREVIEW_POLICY_FORCE_ON,
        PREVIEW_POLICY_FORCE_OFF,
    }:
        raise ValueError(f"invalid CSS preview policy: {PREVIEW_POLICY}")

    if _LEGACY_MARKER.search(source):
        raise ValueError("legacy CSS preview marker present")

    selected_markers = [marker.strip() for marker in _SELECTED_MARKER.findall(source)]
    if selected_markers and selected_markers != [MARKER]:
        raise ValueError("stale, future, or duplicate selected-only preview marker")

    anchor_count = source.count(_ANCHOR)
    training_anchor_count = source.count(_TRAINING_ANCHOR)
    if anchor_count != 1 or training_anchor_count != 1:
        raise ValueError(
            "expected exactly one CSS and training transform anchor, found "
            f"{anchor_count} and {training_anchor_count}"
        )

    start = source.index(_ANCHOR)
    end = source.index(_TRAINING_ANCHOR, start) + len(_TRAINING_ANCHOR)
    region = source[start:end]
    pristine_region = _ANCHOR + _TRAINING_ANCHOR
    block = _BLOCK.replace(
        _WAIT_EACH_IO_TOKEN,
        str(int(PREVIEW_PROBE_WAIT_EACH_IO)),
    )
    for token, label in (
        (_WAIT_UNLOCK_1_TOKEN, "_wait_for_pi_unlock_1"),
        (_WAIT_UNLOCK_2_TOKEN, "_wait_for_pi_unlock_2"),
        (_WAIT_IDENTIFIER_TOKEN, "_wait_for_pi_identifier"),
    ):
        block = block.replace(
            token,
            _pi_wait_block(label) if PREVIEW_PROBE_WAIT_EACH_IO else "",
        )
    canonical_region = _ANCHOR + block + _TRAINING_ANCHOR
    if region == canonical_region:
        transformed = source
    elif region == pristine_region:
        if selected_markers:
            raise ValueError("selected-only preview marker lies outside canonical region")
        transformed = source[:start] + canonical_region + source[end:]
    else:
        raise ValueError("selected-only preview transform region is not pristine or canonical")

    pristine_sync_count = transformed.count(_SYNC_PRISTINE)
    canonical_sync_count = transformed.count(_SYNC_CANONICAL)
    if pristine_sync_count == 1 and canonical_sync_count == 0:
        transformed = transformed.replace(_SYNC_PRISTINE, _SYNC_CANONICAL, 1)
    elif pristine_sync_count != 0 or canonical_sync_count != 1:
        raise ValueError(
            "expected exactly one pristine or canonical dynamic CSS sync scope, found "
            f"{pristine_sync_count} and {canonical_sync_count}"
        )

    pristine_stock_count = transformed.count(_STOCK_INDICATOR_PRISTINE)
    canonical_stock_count = transformed.count(_STOCK_INDICATOR_CANONICAL)
    if canonical_stock_count == 1 and pristine_stock_count == 0:
        return transformed
    if pristine_stock_count == 1 and canonical_stock_count == 0:
        return transformed.replace(
            _STOCK_INDICATOR_PRISTINE,
            _STOCK_INDICATOR_CANONICAL,
            1,
        )
    raise ValueError(
        "expected exactly one pristine or canonical stock-indicator hover guard, found "
        f"{pristine_stock_count} and {canonical_stock_count}"
    )


def transform_boot_title_diagnostic(source: str) -> str:
    """Draw the cached SC64 probe identifier as centered title telemetry."""
    marker_count = source.count(TITLE_DIAGNOSTIC_MARKER)
    if marker_count == 1:
        version_matches = list(_BOOT_VERSION_PATTERN.finditer(source))
        if (
            len(version_matches) == 1
            and source.count(_BOOT_DIAGNOSTIC_DRAW) == 1
            and source.count(_BOOT_PROBE_STRING) == 1
        ):
            return source
        raise ValueError("SummerCart title diagnostic is not canonical")
    if marker_count:
        raise ValueError("duplicate SummerCart title diagnostic marker")

    draw_count = source.count(_BOOT_VERSION_DRAW)
    version_matches = list(_BOOT_VERSION_PATTERN.finditer(source))
    if draw_count != 1 or len(version_matches) != 1:
        raise ValueError(
            "expected exactly one pristine title version draw and string, found "
            f"{draw_count} and {len(version_matches)}"
        )

    transformed = source.replace(_BOOT_VERSION_DRAW, _BOOT_DIAGNOSTIC_DRAW, 1)

    version_match = _BOOT_VERSION_PATTERN.search(transformed)
    if version_match is None:
        raise ValueError("title version string disappeared during diagnostic transform")
    indent = version_match.group("indent")
    probe_string = f"{indent}{_BOOT_PROBE_STRING}"
    return transformed[:version_match.end()] + "\n" + probe_string + transformed[version_match.end():]
