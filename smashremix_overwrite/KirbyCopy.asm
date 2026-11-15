// KirbyCopy.asm (smashremix-extra)
// Helpers for the extra characters' Kirby copy abilities: Kirby's (and J Kirby's) copy of a
// character's neutral special runs the character's own routines, on Kirby actions of its own
// (Character.add_new_action(KIRBY, ...) in the character's code, see extra_characters_guide.md).
// Assembled after Kirby / J Kirby, before the extra characters.
//
// While Kirby runs them, those routines see Kirby's player struct: Kirby's action ids, his
// character id, and his own variables at 0x0ADC.. (0x0ADC: his copied power). These helpers
// let them work for both:
//
//   KirbyCopy.action_map(NAME)               the character's actions and Kirby's / J Kirby's
//   dh <character action>, <Kirby action>, <J Kirby action>   copies of them (several of the
//   ...                                      character's actions may map to one copy; the first
//   KirbyCopy.action_map_end()               one is what the copy stands for)
//
//   jal KirbyCopy.change_action_             instead of jal 0x800E6F24 (change action): a1 = the
//                                            character's action (Kirby / J Kirby: his copy of it)
//   KirbyCopy.load_action(dst, player)       dst = current action (Kirby / J Kirby: the character's
//                                            action his copy stands for)
//   KirbyCopy.load_char_id(dst, player)      dst = character id (Kirby / J Kirby: his copied power's)
//   KirbyCopy.copy_action(reg, player)       reg = the character's action -> Kirby's copy (code that
//                                            changes action without 0x800E6F24, e.g. j 0x8014DB30)
//   KirbyCopy.var_address(dst, player, offset, kirby_offset)
//                                            dst = player + offset (Kirby / J Kirby: + kirby_offset)
//   KirbyCopy.branch_if_kirby(player, tmp, label)
//
// player = a player struct register; dst / tmp must not be it. The macros expand to several
// instructions: never in a delay slot.
//
// Routines for the Kirby actions themselves:
//   KirbyCopy.ground_collision_ / air_collision_   slide off -> the next action (the air version) /
//                                                   land -> the previous one (the ground version)
//   KirbyCopy.ground_nsp_initial_ / air_nsp_initial_  kirby_ground_nsp / kirby_air_nsp for a
//                                                   character whose neutral special is the vanilla
//                                                   0xE4 / 0xE5 action

scope KirbyCopy {
    // @ Description
    // The action maps, by copied power (character id): 0 = none
    action_map_table:
    constant ACTION_MAP_TABLE_ORIGIN(origin())
    fill Character.NUM_CHARACTERS * 4

    macro action_map(name) {
        OS.align(4)
        kirby_copy_action_map:
        pushvar origin, base
        origin KirbyCopy.ACTION_MAP_TABLE_ORIGIN + (Character.id.{name} * 4)
        dw kirby_copy_action_map
        pullvar base, origin
    }

    macro action_map_end() {
        dh 0xFFFF, 0xFFFF, 0xFFFF
        OS.align(4)
    }

    // @ Description
    // Status overrides, by copied power (character id): 0 = none. For a copy built on one of Kirby's
    // own copy abilities (a DK clone's Giant Punch: Kirby's DK punch): while Kirby / J Kirby has that
    // power, his status <status> runs the action entry of <Kirby action> / <J Kirby action>
    // instead (its routines and parameters: animation, moveset), keeping the status id (Kirby's
    // own code for that copy checks and sets it).
    //   KirbyCopy.status_overrides(NAME)
    //   dh <Kirby status>, <Kirby action>, <J Kirby action>
    //   ...
    //   KirbyCopy.status_overrides_end()
    status_override_table:
    constant STATUS_OVERRIDE_TABLE_ORIGIN(origin())
    fill Character.NUM_CHARACTERS * 4

    macro status_overrides(name) {
        OS.align(4)
        kirby_copy_status_overrides:
        pushvar origin, base
        origin KirbyCopy.STATUS_OVERRIDE_TABLE_ORIGIN + (Character.id.{name} * 4)
        dw kirby_copy_status_overrides
        pullvar base, origin
    }

    macro status_overrides_end() {
        dh 0xFFFF, 0xFFFF, 0xFFFF
        OS.align(4)
    }

    // @ Description
    // ftMainSetStatus (0x800E6F24), special statuses (>= 0xDC): v0 = status id, t0 = its index in
    // the character's action array (v0 - 0xDC), t9 = the array, s1 = player struct. Kirby / J Kirby
    // with a copied power that overrides this status: the index of the override's entry.
    scope status_override_: {
        OS.patch_start(0x62BE4, 0x800E73E4)
        j       status_override_
        sw      t9, 0x0080(sp)              // original line 3 (the action array)
        OS.patch_end()

        lw      t1, 0x0008(s1)              // t1 = character id
        lli     t2, Character.id.KIRBY      // ~
        beq     t1, t2, _copy               // Kirby: his column
        lli     t3, 0x0002                  // t3 = Kirby's column (bytes)
        lli     t2, Character.id.JKIRBY     // ~
        bne     t1, t2, _end                // not J Kirby either: unchanged
        lli     t3, 0x0004                  // t3 = J Kirby's column (bytes)

        _copy:
        lw      t1, 0x0ADC(s1)              // t1 = copied power (character id)
        sltiu   t2, t1, Character.NUM_CHARACTERS
        beqz    t2, _end                    // (none)
        sll     t1, t1, 0x0002              // t1 = offset in status_override_table
        li      t2, status_override_table   // ~
        addu    t2, t2, t1                  // ~
        lw      t2, 0x0000(t2)              // t2 = overrides
        beqz    t2, _end                    // none: unchanged
        nop

        _loop:
        lhu     t1, 0x0000(t2)              // t1 = status
        lli     t4, 0xFFFF                  // ~
        beq     t1, t4, _end                // end of the list: unchanged
        addu    t4, t2, t3                  // ~
        bne     t1, v0, _loop               // not this status: next
        addiu   t2, t2, 0x0006              // ~
        lhu     t1, 0x0000(t4)              // t1 = the override's action
        addiu   t0, t1, -0x00DC             // t0 = its index in the action array

        _end:
        j       0x800E7420                  // original line 2 (branch), with...
        sw      t0, 0x0074(sp)              // original line 1: the index
    }

    // @ Description
    // a0 = player struct, a1 = action, a2 = direction (0: the character's -> Kirby's copy,
    // 1: Kirby's copy -> the character's) -> v0 = the action (a1 when not Kirby / J Kirby, or not
    // in the map). Only v0 changes.
    scope translate_: {
        addiu   sp, sp, -0x0018             // allocate stack space
        sw      t0, 0x0004(sp)              // ~
        sw      t1, 0x0008(sp)              // ~
        sw      t2, 0x000C(sp)              // ~
        sw      t3, 0x0010(sp)              // save registers
        or      v0, a1, r0                  // v0 = action (unchanged)
        lw      t0, 0x0008(a0)              // t0 = character id
        lli     t1, Character.id.KIRBY      // ~
        beq     t0, t1, _copy               // Kirby: his column
        lli     t2, 0x0002                  // t2 = Kirby's column (bytes)
        lli     t1, Character.id.JKIRBY     // ~
        bne     t0, t1, _end                // not J Kirby either: unchanged
        lli     t2, 0x0004                  // t2 = J Kirby's column (bytes)

        _copy:
        lw      t0, 0x0ADC(a0)              // t0 = copied power (character id)
        sltiu   t1, t0, Character.NUM_CHARACTERS
        beqz    t1, _end                    // (none)
        sll     t0, t0, 0x0002              // t0 = offset in action_map_table
        li      t1, action_map_table        // ~
        addu    t1, t1, t0                  // ~
        lw      t1, 0x0000(t1)              // t1 = action map
        beqz    t1, _end                    // no map: unchanged
        nop

        _loop:
        lhu     t0, 0x0000(t1)              // t0 = the character's action
        lli     t3, 0xFFFF                  // ~
        beq     t0, t3, _end                // end of the map: unchanged
        addu    t3, t1, t2                  // ~
        lhu     t3, 0x0000(t3)              // t3 = Kirby's / J Kirby's copy
        bnez    a2, _back                   // copy -> character
        nop
        beql    t0, a1, _end                // found: the copy
        or      v0, t3, r0                  // ~
        b       _loop                       // next
        addiu   t1, t1, 0x0006              // ~
        _back:
        beql    t3, a1, _end                // found: the character's action
        or      v0, t0, r0                  // ~
        b       _loop                       // next
        addiu   t1, t1, 0x0006              // ~

        _end:
        lw      t0, 0x0004(sp)              // ~
        lw      t1, 0x0008(sp)              // ~
        lw      t2, 0x000C(sp)              // ~
        lw      t3, 0x0010(sp)              // restore registers
        jr      ra                          // return
        addiu   sp, sp, 0x0018              // deallocate stack space
    }

    // @ Description
    // 0x800E6F24 (change action) with the character's action id: a0 = player object, a1 = action,
    // a2 = starting frame, a3 = frame speed multiplier, 0x0010(sp) = argument 4 (as 0x800E6F24)
    scope change_action_: {
        addiu   sp, sp, -0x0020             // allocate stack space
        sw      ra, 0x0014(sp)              // ~
        sw      a0, 0x0018(sp)              // ~
        sw      a2, 0x001C(sp)              // save registers
        lw      a0, 0x0084(a0)              // a0 = player struct
        jal     translate_                  // v0 = Kirby's copy of the action (if Kirby)
        or      a2, r0, r0                  // a2 = character -> copy
        or      a1, v0, r0                  // a1 = action
        lw      ra, 0x0014(sp)              // ~
        lw      a0, 0x0018(sp)              // ~
        lw      a2, 0x001C(sp)              // restore registers
        j       0x800E6F24                  // change action
        addiu   sp, sp, 0x0020              // deallocate stack space
    }

    macro load_action(dst, player) {
        addiu   sp, sp, -0x0020             // allocate stack space
        sw      ra, 0x0010(sp)              // ~
        sw      a0, 0x0014(sp)              // ~
        sw      a1, 0x0018(sp)              // ~
        sw      a2, 0x001C(sp)              // ~
        sw      v0, 0x000C(sp)              // save registers
        or      a0, {player}, r0            // a0 = player struct
        lw      a1, 0x0024(a0)              // a1 = current action
        jal     KirbyCopy.translate_        // v0 = the character's action
        lli     a2, 0x0001                  // a2 = copy -> character
        sw      v0, 0x0008(sp)              // 0x0008(sp) = action
        lw      ra, 0x0010(sp)              // ~
        lw      a0, 0x0014(sp)              // ~
        lw      a1, 0x0018(sp)              // ~
        lw      a2, 0x001C(sp)              // ~
        lw      v0, 0x000C(sp)              // restore registers
        lw      {dst}, 0x0008(sp)           // dst = action
        addiu   sp, sp, 0x0020              // deallocate stack space
    }

    // reg = the character's action -> Kirby's / J Kirby's copy of it (for code that changes action
    // some other way than 0x800E6F24, e.g. jumping into a vanilla routine with a1 = the action)
    macro copy_action(reg, player) {
        addiu   sp, sp, -0x0028             // allocate stack space
        sw      {reg}, 0x0008(sp)           // 0x0008(sp) = action
        sw      {player}, 0x0004(sp)        // 0x0004(sp) = player struct
        sw      ra, 0x0010(sp)              // ~
        sw      a0, 0x0014(sp)              // ~
        sw      a1, 0x0018(sp)              // ~
        sw      a2, 0x001C(sp)              // ~
        sw      v0, 0x000C(sp)              // save registers
        lw      a0, 0x0004(sp)              // a0 = player struct
        lw      a1, 0x0008(sp)              // a1 = action
        jal     KirbyCopy.translate_        // v0 = the copy (Kirby / J Kirby)
        or      a2, r0, r0                  // a2 = character -> copy
        sw      v0, 0x0008(sp)              // 0x0008(sp) = action
        lw      ra, 0x0010(sp)              // ~
        lw      a0, 0x0014(sp)              // ~
        lw      a1, 0x0018(sp)              // ~
        lw      a2, 0x001C(sp)              // ~
        lw      v0, 0x000C(sp)              // restore registers
        lw      {reg}, 0x0008(sp)           // reg = action
        addiu   sp, sp, 0x0028              // deallocate stack space
    }

    macro load_char_id(dst, player) {
        lw      {dst}, 0x0008({player})     // dst = character id
        addiu   {dst}, {dst}, -Character.id.KIRBY
        beql    {dst}, r0, pc() + 0x18      // Kirby: his copied power
        lw      {dst}, 0x0ADC({player})     // ~
        addiu   {dst}, {dst}, Character.id.KIRBY - Character.id.JKIRBY
        beql    {dst}, r0, pc() + 0x0C      // J Kirby: his copied power
        lw      {dst}, 0x0ADC({player})     // ~
        lw      {dst}, 0x0008({player})     // dst = character id
    }

    macro var_address(dst, player, offset, kirby_offset) {
        lw      {dst}, 0x0008({player})     // dst = character id
        addiu   {dst}, {dst}, -Character.id.KIRBY
        beql    {dst}, r0, pc() + 0x18      // Kirby: his own variable
        addiu   {dst}, {player}, {kirby_offset}
        addiu   {dst}, {dst}, Character.id.KIRBY - Character.id.JKIRBY
        beql    {dst}, r0, pc() + 0x0C      // J Kirby: his own variable
        addiu   {dst}, {player}, {kirby_offset}
        addiu   {dst}, {player}, {offset}   // dst = the character's variable
    }

    macro branch_if_kirby(player, tmp, label) {
        lw      {tmp}, 0x0008({player})     // tmp = character id
        addiu   {tmp}, {tmp}, -Character.id.KIRBY
        beq     {tmp}, r0, {label}          // Kirby
        addiu   {tmp}, {tmp}, Character.id.KIRBY - Character.id.JKIRBY
        beq     {tmp}, r0, {label}          // J Kirby
        nop
    }

    // @ Description
    // Ground collision for a copy whose air version is the next action (added right after it):
    // slid off the floor -> the air version, same frame (vanilla neutral specials' 0xE4 -> 0xE5)
    scope ground_collision_: {
        addiu   sp, sp,-0x0018              // allocate stack space
        sw      ra, 0x0014(sp)              // store ra
        li      a1, ground_to_air_          // a1(transition subroutine) = ground_to_air_
        jal     0x800DDDDC                  // common ground collision subroutine (transition on no floor, slide-off)
        nop
        lw      ra, 0x0014(sp)              // load ra
        jr      ra                          // return
        addiu   sp, sp, 0x0018              // deallocate stack space
    }

    scope ground_to_air_: {
        addiu   sp, sp,-0x0038              // allocate stack space
        sw      ra, 0x001C(sp)              // store ra
        sw      a0, 0x0038(sp)              // 0x0038(sp) = player object
        lw      a0, 0x0084(a0)              // a0 = player struct
        jal     0x800DEEC8                  // set aerial state
        sw      a0, 0x0034(sp)              // 0x0034(sp) = player struct
        lw      v0, 0x0034(sp)              // v0 = player struct
        lw      a0, 0x0038(sp)              // a0 = player object
        lw      t7, 0x0024(v0)              // t7 = current action
        addiu   a1, t7, 0x0001              // a1 = the air version
        lw      a2, 0x0078(a0)              // a2(starting frame) = current animation frame
        lui     a3, 0x3F80                  // a3(frame speed multiplier) = 1.0
        lli     t6, 0x0001                  // ~
        jal     0x800E6F24                  // change action
        sw      t6, 0x0010(sp)              // argument 4 = 1 (continue hitbox)
        jal     0x800D8EB8                  // momentum capture
        lw      a0, 0x0034(sp)              // a0 = player struct
        lw      ra, 0x001C(sp)              // load ra
        addiu   sp, sp, 0x0038              // deallocate stack space
        jr      ra                          // return
        nop
    }

    // @ Description
    // Air collision for a copy whose ground version is the previous action: landing -> the ground
    // version, same frame (vanilla neutral specials' 0xE5 -> 0xE4)
    scope air_collision_: {
        addiu   sp, sp,-0x0018              // allocate stack space
        sw      ra, 0x0014(sp)              // store ra
        li      a1, air_to_ground_          // a1(transition subroutine) = air_to_ground_
        jal     0x800DE6E4                  // common air collision subroutine (transition on landing, no ledge grab)
        nop
        lw      ra, 0x0014(sp)              // load ra
        jr      ra                          // return
        addiu   sp, sp, 0x0018              // deallocate stack space
    }

    scope air_to_ground_: {
        addiu   sp, sp,-0x0038              // allocate stack space
        sw      ra, 0x001C(sp)              // store ra
        sw      a0, 0x0038(sp)              // 0x0038(sp) = player object
        lw      a0, 0x0084(a0)              // a0 = player struct
        jal     0x800DEE98                  // set grounded state
        sw      a0, 0x0034(sp)              // 0x0034(sp) = player struct
        lw      v0, 0x0034(sp)              // v0 = player struct
        lw      a0, 0x0038(sp)              // a0 = player object
        lw      t7, 0x0024(v0)              // t7 = current action
        addiu   a1, t7, -0x0001             // a1 = the ground version
        lw      a2, 0x0078(a0)              // a2(starting frame) = current animation frame
        lui     a3, 0x3F80                  // a3(frame speed multiplier) = 1.0
        lli     t6, 0x0001                  // ~
        jal     0x800E6F24                  // change action
        sw      t6, 0x0010(sp)              // argument 4 = 1 (continue hitbox)
        lw      ra, 0x001C(sp)              // load ra
        addiu   sp, sp, 0x0038              // deallocate stack space
        jr      ra                          // return
        nop
    }

    // @ Description
    // Kirby's / J Kirby's neutral special for a copied power whose own is the vanilla 0xE4 (ground) /
    // 0xE5 (air) action (its routines edited by the character): to Kirby's copy of it.
    scope ground_nsp_initial_: {
        b       nsp_initial_
        lli     a1, 0x00E4                  // a1 = the character's ground neutral special
    }

    scope air_nsp_initial_: {
        b       nsp_initial_
        lli     a1, 0x00E5                  // a1 = the character's air neutral special
    }

    // a0 = player object, a1 = the character's action
    scope nsp_initial_: {
        addiu   sp, sp, -0x0020             // allocate stack space
        sw      ra, 0x001C(sp)              // ~
        sw      a0, 0x0020(sp)              // 0x0020(sp) = player object
        sw      r0, 0x0010(sp)              // argument 4 = 0
        or      a2, r0, r0                  // a2 = float: 0.0
        jal     change_action_              // change action
        lui     a3, 0x3F80                  // a3 = float: 1.0
        lw      t0, 0x0020(sp)              // ~
        lw      t0, 0x0084(t0)              // t0 = player struct
        sw      r0, 0x017C(t0)              // moveset flags = 0 (Kirby's own actions may leave them set:
        sw      r0, 0x0180(t0)              // his copy's routines would act on them on frame 1)
        sw      r0, 0x0184(t0)              // ~
        jal     0x800E0830                  // unknown common subroutine
        lw      a0, 0x0020(sp)              // a0 = player object
        lw      ra, 0x001C(sp)              // ~
        jr      ra                          // return
        addiu   sp, sp, 0x0020              // deallocate stack space
    }
}
