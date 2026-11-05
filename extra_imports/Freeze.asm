// Freeze.asm
if !{defined __FREEZE__} {
define __FREEZE__()
print "included Freeze.asm\n"


// Frozen state, based on how freezing works in Smash Ultimate. Shared by all characters:
// any hitbox (fighter, weapon or item) with damage type Freeze.ELEMENT can freeze.
// The freezing damage type (5) is unused in vanilla.
// - A freezing hit freezes if its knockback is at least KNOCKBACK_MIN, or if the freeze meter (freeze time
//   of freezing hits, adding up while the fighter stays in hitstun) goes over METER_THRESHOLD.
//   Grabbed or trapped fighters can't be frozen.
// - Freeze time = 5 * damage + 0.5 * percent (before the hit), up to MAX_TIME frames (damage counts 1.2x, see below)
// - Frozen fighters keep their knockback (decaying slower), fall slower and can't act or be grabbed.
//   Mashing shortens the freeze. Hits deal half damage and quarter knockback and change the freeze time
//   (see frozen_hit_time_)
// - When the freeze ends, the ice shatters and the fighter pops up, and can't be frozen again for IMMUNE_TIME frames
scope Freeze {
    constant DEBUG_ALWAYS_FREEZE(0)         // 1 = every freezing hit freezes (ignores meter, knockback and immunity), for testing
    constant ELEMENT(5)                     // freezing damage type
    constant FIRE_ELEMENT(1)                // fire damage type
    constant MAX_TIME(250)                  // max freeze time, in frames
    // Ultimate's values assume its 1.2x damage multiplier in 1v1, which we don't use, so we compensate:
    // knockback needed is divided by 1.2, and damage counts 1.2x in the freeze time formulas
    constant KNOCKBACK_MIN(0x422F0000)      // 43.75 (52.5 / 1.2) - knockback needed to freeze with a single hit
    constant METER_THRESHOLD(263)           // freeze meter needed to freeze with hits while in hitstun (same as Ultimate)
    constant IMMUNE_TIME(60)                // frames after thawing in which the fighter can't be frozen
    constant MASH_STICK_TENTHS(40)          // freeze time removed per stick mash input, in tenths of a frame (4)
    constant MASH_BUTTON_TENTHS(72)         // freeze time removed per button mash input, in tenths of a frame (7.2)
    constant DECAYING_HITS(5)               // hits while frozen that use the decaying freeze time (see frozen_hit_time_)
    // Damage taken while frozen is halved (rounded up) only when it's added to the percent (see update_damage_),
    // so knockback is still calculated with the full damage and KNOCKBACK_MUL is the only knockback reduction
    constant KNOCKBACK_MUL(0x3E800000)      // 0.25 - knockback multiplier for hits while frozen, like Ultimate
    constant SPIN_MUL(0x3BA3D70A)           // 0.005 - frozen fighters spin this many radians per frame per unit of speed
    constant DECAY_COMPENSATION(0x3F333333) // 0.7 - knockback decays by 1.0 per frame instead of 1.7, like Ultimate (0.03 instead of 0.051: 1.7 * 0.03 / 0.051 = 1.0)
    constant TVEL_MUL(0x3F333333)           // 0.7 - falling speed multiplier while frozen, like Ultimate
    constant ESCAPE_VEL_Y(0x42700000)       // 60.0 - Y velocity when breaking out
    constant ACTION(Action.DamageFlyTop)      // pose while frozen (with animation speed 0)
    constant ICE_FGM(FGM.ICE_HIT)           // played when frozen, on hits while frozen and when the ice breaks (extra_imports/Freeze/sounds/ice_hit)
    constant SHATTER_PRIM_COLOR(0xA0E0FF)   // RGB tint for the shatter effect
    constant SHATTER_ENV_COLOR(0x3080FF)    // RGB tint for the shatter effect

    constant BLOCK_SIZE_MUL(0x40200000)     // 2.5 - block side = this * max(ECB height, ECB width) * root bone scale
    constant HURTBOX_MUL(0x3ED9999A)        // 0.425 - half side of the frozen hurtbox cube, relative to the block's
    constant BLOCK_HALF_SIZE(120)           // half the side of the block quad, before scaling
    constant BLOCK_QUAD_SIZE(0x43700000)    // 240.0 - side of the block quad before scaling (2 * BLOCK_HALF_SIZE)
    constant BLOCK_COLOR(0xE0F4FFC4)        // RGBA for the brightest texels (white) and the block's opacity
    constant BLOCK_DARK_COLOR(0x0038A0FF)   // RGB for the darkest texels (blue); more saturated than ice256.png, which reads gray
    constant SHUFFLE_POSITIONS(0x8012B930)  // dFTDisplayMainShufflePositions: Vec2f[2][4] (normal, electric)

    // Ice block texture (extra_imports/Freeze/ice64.png), in a file loaded at the start of every battle (see make_effect_)
    constant TEX_W(TEXTURES.ICE_BLOCK.WIDTH)
    constant TEX_H(TEXTURES.ICE_BLOCK.HEIGHT)

    // @ Description
    // Sets reg to 1 if the fighter is grabbed or trapped (grabbed, inhaled, in an egg, Falcon Dive, thrown...),
    // since they can't be frozen then. Uses at.
    // fp - fighter struct register
    macro is_captured(fp, reg) {
        lw      {reg}, 0x0024({fp})         // reg = current action
        addiu   {reg}, {reg}, -Action.CapturePulled
        sltiu   {reg}, {reg}, Action.ThrownFoxB - Action.CapturePulled + 1 // reg = 1 if CapturePulled <= action <= ThrownFoxB
        lw      at, 0x0844({fp})            // at = captured by
        sltu    at, r0, at                  // at = 1 if captured by someone
        or      {reg}, {reg}, at
    }

    // @ Description
    // Per-fighter freeze info that has to persist between statuses
    // 0x0 - fighter struct, 0x4 - freeze meter, 0x8 - frame the hitstun of the last hit ends, 0xC - frame of last thaw,
    // 0x10 - remaining freeze time when a hit took the fighter out of the frozen state (to refreeze them after it),
    // 0x14 - frame of the last hit that took the fighter out of a grabbed/trapped status (they can't freeze from it),
    // 0x18 - hits taken in the current freeze, 0x1C - leftover mash time, in tenths of a frame,
    // 0x20 - spin angle (f32, radians, clockwise on screen),
    // 0x24 - body root joint rotation x, y, z from the animation (the pose spin_ rotates),
    // 0x30 - body root joint rotation x, y, z spin_ wrote last (if it changed, the animation wrote a new pose)
    constant ENTRY_SIZE(0x3C)
    constant NO_SPIN(0x7FFFFFFF)            // a "rotation spin_ wrote last" that never matches, to read the pose again
    constant NUM_ENTRIES(8)
    entries:
    fill ENTRY_SIZE * NUM_ENTRIES

    // @ Description
    // Ice block: a textured quad that always faces the camera (billboard) and is drawn in front of fighters.
    // The effect's main DObj follows the fighter and scales to its size; its child moves the quad up to the
    // center of the fighter's body and faces it to the camera (transform kind 0x2E).
    OS.align(4)
    effect_desc:
    db 0x03                                 // flags: effect struct + main DObj with one child
    db 0x0B                                 // DL link (drawn after fighters, which use 9)
    dh 0
    dw effect_file_head                     // file head (0: the offsets below are absolute addresses)
    db 0x50, 0x20, 0x00                     // main DObj transforms: follow joint in user_data, scale
    db 0x12, 0x2E, 0x00                     // child DObj transforms: translate, billboard
    dh 0
    dw effect_update_                       // update
    dw effect_display_                      // display
    dw block_dl_links                       // child DObj display list links
    dw 0                                    // MObjSub
    dw 0                                    // AnimJoint
    dw 0                                    // MatAnimJoint

    effect_file_head:
    dw 0

    // @ Description
    // Display list links for the block: drawn in the translucent display list
    block_dl_links:
    dw 1, block_dl
    dw 4, 0                                 // end

    // @ Description
    // Block quad vertices: x, y, z, flag, s, t, rgba
    OS.align(8)
    block_vertices:
    dh -BLOCK_HALF_SIZE, -BLOCK_HALF_SIZE, 0, 0, TEX_W << 5, TEX_H << 5; dw 0xFFFFFFFF
    dh BLOCK_HALF_SIZE, -BLOCK_HALF_SIZE, 0, 0, 0, TEX_H << 5; dw 0xFFFFFFFF
    dh BLOCK_HALF_SIZE, BLOCK_HALF_SIZE, 0, 0, 0, 0; dw 0xFFFFFFFF
    dh -BLOCK_HALF_SIZE, BLOCK_HALF_SIZE, 0, 0, TEX_W << 5, 0; dw 0xFFFFFFFF

    // @ Description
    // Block display list: texture brightness blends BLOCK_DARK_COLOR to BLOCK_COLOR, alpha blended, without depth test (always in front)
    block_dl:
    dw 0xE7000000, 0                        // pipe sync
    dw 0xD9FDF9FF, 0                        // clear lighting and culling
    dw 0xE3000A01, 0x00000000               // 1 cycle
    dw 0xE3001001, 0x00000000               // no TLUT
    dw 0xE200001C, 0x00504240               // render mode: G_RM_XLU_SURF | G_RM_XLU_SURF2 (no depth test)
    dw 0xFC309661, 0x552EFF7F               // combine: color = (PRIMITIVE - ENVIRONMENT) * TEXEL0 + ENVIRONMENT, alpha = TEXEL0 * PRIMITIVE
    dw 0xFA000000, BLOCK_COLOR              // prim color
    dw 0xFB000000, BLOCK_DARK_COLOR         // env color
    dw 0xD7000002, 0xFFFFFFFF               // texture on
    block_dl_texture:
    dw TEXTURES.ICE_BLOCK.TIMG_W0, 0        // set texture image (address set in make_effect_)
    dw TEXTURES.ICE_BLOCK.LOAD_TILE_W0, 0x07000000 // load tile
    dw 0xE6000000, 0                        // load sync
    dw 0xF3000000, TEXTURES.ICE_BLOCK.LOAD_BLOCK_W1 // load block
    dw 0xE7000000, 0                        // pipe sync
    dw TEXTURES.ICE_BLOCK.RENDER_TILE_W0, 0x00080200 // render tile, clamp
    dw 0xF2000000, TEXTURES.ICE_BLOCK.TILE_SIZE_W1 // tile size
    dw 0x01004008, block_vertices           // 4 vertices
    dw 0x06000204, 0x00000406               // 2 triangles
    dw 0xE7000000, 0                        // pipe sync
    dw 0xE200001C, 0x005049D8               // render mode back to G_RM_AA_ZB_XLU_SURF | G_RM_AA_ZB_XLU_SURF2
    dw 0xDF000000, 0                        // end

    // @ Description
    // Returns the freeze info entry for a fighter, creating it if needed
    // @ Arguments
    // a0 - fighter struct
    // @ Returns
    // v0 - entry, or 0 if there's no free entry
    scope get_entry_: {
        li      v0, entries
        lli     t0, NUM_ENTRIES
        or      t1, r0, r0                  // t1 = first free entry

        _loop:
        lw      t2, 0x0000(v0)              // t2 = entry fighter struct
        beq     t2, a0, _end                // if it's this fighter's entry, return it
        nop
        bnez    t2, _next                   // skip if entry is used
        nop
        beqzl   t1, _next                   // if no free entry yet...
        or      t1, v0, r0                  // ...this is the first free entry
        _next:
        addiu   t0, t0, -0x0001
        bnez    t0, _loop
        addiu   v0, v0, ENTRY_SIZE

        or      v0, t1, r0                  // v0 = first free entry
        beqz    v0, _end                    // if there's none, return 0
        nop
        sw      a0, 0x0000(v0)              // entry fighter struct = this fighter
        sw      r0, 0x0004(v0)              // meter = 0
        sw      r0, 0x0008(v0)              // hitstun end frame = 0
        sw      r0, 0x0018(v0)              // hits in current freeze = 0
        sw      r0, 0x001C(v0)              // leftover mash time = 0
        sw      r0, 0x0020(v0)              // spin angle = 0
        sw      r0, 0x0024(v0)
        sw      r0, 0x0028(v0)
        sw      r0, 0x002C(v0)              // pose rotation = 0
        li      t2, NO_SPIN
        sw      t2, 0x0030(v0)              // read the pose on the next spin
        sw      r0, 0x000C(v0)              // last thaw frame = 0
        sw      r0, 0x0010(v0)              // pending freeze time = 0
        addiu   t2, r0, -0x0001
        sw      t2, 0x0014(v0)              // last hit while captured frame = none (-1)

        _end:
        jr      ra
        nop
    }

    // @ Description
    // Clears the freeze info entry of a fighter. Called when a fighter is spawned.
    // @ Arguments
    // a0 - fighter object
    scope reset_entry_: {
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        li      v0, entries
        lli     t0, NUM_ENTRIES

        _loop:
        lw      t2, 0x0000(v0)              // t2 = entry fighter struct
        beql    t2, a0, _next               // if it's this fighter's entry...
        sw      r0, 0x0000(v0)              // ...free it
        _next:
        addiu   t0, t0, -0x0001
        bnez    t0, _loop
        addiu   v0, v0, ENTRY_SIZE

        jr      ra
        nop
    }

    // @ Description
    // Hooks the start of ftManagerInitFighter (spawn and respawn) to clear the fighter's freeze info
    scope init_fighter_: {
        OS.patch_start(0x531F0, 0x800D79F0)
        j       init_fighter_
        nop
        _return:
        OS.patch_end()

        addiu   sp, sp, -0x30               // original line 1
        sw      ra, 0x0024(sp)              // original line 2 (ra is restored before returning)
        sw      a0, 0x0030(sp)              // save a0 (home space)
        jal     reset_entry_
        sw      a1, 0x0034(sp)              // save a1 (home space)
        lw      a0, 0x0030(sp)
        jal     reset_spin_                 // in case they died while frozen and spinning
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        lw      a0, 0x0030(sp)
        j       _return
        lw      a1, 0x0034(sp)
    }

    // @ Description
    // Returns the freeze time for the current hit: 5 * (1.2 * damage) + 0.5 * percent (before the hit),
    // like Ultimate with its 1v1 1.2x damage multiplier (5 * 1.2 = 6)
    // @ Arguments
    // a0 - fighter struct
    // a1 - damage of the hit
    // a2 - damage added to the percent by the hit
    // @ Returns
    // v0 - freeze time
    scope get_freeze_time_: {
        lw      t1, 0x002C(a0)              // t1 = percent (after the hit)
        subu    t1, t1, a2                  // t1 = percent before the hit
        bltzl   t1, pc() + 8                // if negative...
        or      t1, r0, r0                  // ...use 0
        srl     t1, t1, 0x0001              // t1 = percent / 2
        sll     v0, a1, 0x0001              // v0 = damage * 2
        addu    v0, v0, a1                  // v0 = damage * 3
        sll     v0, v0, 0x0001              // v0 = damage * 6
        jr      ra
        addu    v0, v0, t1                  // v0 = 6 * damage + percent / 2
    }

    // @ Description
    // 0.67^c for the first DECAYING_HITS hits while frozen, in thousandths
    decay_table:
    dw 670, 449, 301, 202, 135

    // @ Description
    // Returns the new freeze time after a hit while frozen, like Ultimate. With c = hits taken in this freeze
    // (counting this one), f = freeze time of the hit (see get_freeze_time_) * 0.67^c and d = damage * 1.2:
    // - hits 1 to DECAYING_HITS, while under MAX_TIME: freezing hits add f (up to MAX_TIME), fire hits set it
    //   to f, other hits remove 4d - f
    // - later hits, or at MAX_TIME: freezing hits don't change it, fire hits set it to 0, other hits remove 4d
    // @ Arguments
    // a0 - fighter struct
    // a1 - freeze time before the hit
    // a2 - entry
    // @ Returns
    // v0 - new freeze time (<= 0 = thaw)
    scope frozen_hit_time_: {
        addiu   sp, sp, -0x28
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)
        sw      a1, 0x001C(sp)
        sw      a2, 0x0020(sp)

        lw      t0, 0x0018(a2)              // t0 = hits in current freeze
        addiu   t0, t0, 0x0001
        sw      t0, 0x0018(a2)              // c = hits in current freeze + 1

        lw      a1, 0x07F0(a0)              // a1 = damage of the hit
        sw      a1, 0x0024(sp)              // save damage of the hit
        addiu   a2, a1, 0x0001
        jal     get_freeze_time_            // v0 = freeze time of the hit (only uses t1)
        srl     a2, a2, 0x0001              // a2 = damage added to the percent (halved while frozen, see update_damage_)
        lw      a0, 0x0018(sp)
        lw      a1, 0x001C(sp)
        lw      a2, 0x0020(sp)

        lw      t0, 0x0024(sp)              // t0 = damage of the hit
        sll     t1, t0, 0x0003              // t1 = damage * 8
        sll     t2, t0, 0x0004              // t2 = damage * 16
        addu    t1, t1, t2                  // t1 = damage * 24
        lli     at, 5
        divu    t1, at
        mflo    t1                          // t1 = 4 * (damage * 1.2) = damage * 24 / 5
        lw      t2, 0x07F8(a0)              // t2 = damage type
        lw      t3, 0x0018(a2)              // t3 = c

        sltiu   at, t3, DECAYING_HITS + 1
        beqz    at, _late                   // if c > DECAYING_HITS, use the late rules
        nop
        sltiu   at, a1, MAX_TIME
        beqz    at, _late                   // if at MAX_TIME, use the late rules
        nop

        // f = v0 * 0.67^c
        li      t4, decay_table
        addiu   t3, t3, -0x0001
        sll     t3, t3, 0x0002
        addu    t4, t4, t3
        lw      t4, 0x0000(t4)              // t4 = 0.67^c, in thousandths
        multu   v0, t4
        mflo    t4
        lli     at, 1000
        divu    t4, at
        mflo    t4                          // t4 = f

        lli     at, ELEMENT
        beq     t2, at, _early_freezing
        nop
        lli     at, FIRE_ELEMENT
        beq     t2, at, _end
        or      v0, t4, r0                  // fire: freeze time = f
        subu    v0, t1, t4                  // v0 = 4d - f
        b       _end
        subu    v0, a1, v0                  // other: freeze time -= 4d - f

        _early_freezing:
        addu    v0, a1, t4                  // freezing: freeze time += f
        sltiu   at, v0, MAX_TIME + 1
        beqzl   at, pc() + 8                // if over the limit...
        lli     v0, MAX_TIME                // ...use the limit
        b       _end
        nop

        _late:
        lli     at, ELEMENT
        beq     t2, at, _end
        or      v0, a1, r0                  // freezing: no change
        lli     at, FIRE_ELEMENT
        beq     t2, at, _end
        or      v0, r0, r0                  // fire: thaw
        subu    v0, a1, t1                  // other: freeze time -= 4d

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x28
    }

    // @ Description
    // Hooks the start of ftCommonDamageGotoDamageStatus, before the hit changes the fighter's status:
    // - if the fighter is grabbed or trapped, saves the frame so the hit can't freeze them (see on_damage_)
    // - if a hit is taking a frozen fighter out of the frozen state, saves the remaining freeze time to
    //   refreeze them after it (see on_damage_)
    scope damage_status_start_: {
        OS.patch_start(0xBBFA0, 0x80141560)
        j       damage_status_start_
        nop
        _return:
        OS.patch_end()

        addiu   sp, sp, -0x38               // original line 1
        sw      ra, 0x0034(sp)              // original line 2 (ra is restored before returning)

        sw      a0, 0x0038(sp)              // save fighter object (home space for a0)
        lw      t0, 0x0084(a0)              // t0 = fighter struct
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        or      a0, t0, r0
        lw      a0, 0x0038(sp)              // restore fighter object
        beqz    v0, _end
        nop
        lw      t0, 0x0084(a0)              // t0 = fighter struct

        is_captured(t0, t1)                 // t1 = 1 if grabbed or trapped
        beqz    t1, _check_frozen
        nop
        OS.read_word(Global.current_screen_frame_count, t1) // t1 = current frame
        sw      t1, 0x0014(v0)              // save frame of hit while captured

        _check_frozen:
        lw      t1, 0x09D4(t0)              // t1 = update routine
        li      t2, frozen_update_
        bne     t1, t2, _end                // skip if not frozen
        nop
        lw      t1, 0x026C(t0)              // t1 = remaining freeze time
        sw      t1, 0x0010(v0)              // save pending freeze time

        _end:
        j       _return
        nop
    }

    // @ Description
    // Hooks the ftParamUpdateDamage call in ftMainProcParams, which adds a hit's damage to the percent after its
    // knockback was calculated: frozen fighters take half damage (rounded up), without it lowering the knockback
    scope update_damage_: {
        OS.patch_start(0x61B6C, 0x800E636C)
        jal     update_damage_
        lw      a1, 0x07F0(s0)              // original line 2: a1 = damage
        OS.patch_end()

        or      a0, s0, r0                  // a0 = fighter struct
        lw      t0, 0x09D4(a0)              // t0 = update routine
        li      t1, frozen_update_
        bne     t0, t1, _end                // skip if not frozen
        nop
        addiu   a1, a1, 0x0001
        srl     a1, a1, 0x0001              // a1 = damage * 0.5, rounded up

        _end:
        j       0x800EA248                  // original line 1: ftParamUpdateDamage(fp, damage)
        nop
    }

    // @ Description
    // Hooks ftMainProcParams right after a hit's damage is handled (whether or not it changed the fighter's status),
    // so it runs once for every hit that deals damage
    scope damage_processed_: {
        OS.patch_start(0x61C2C, 0x800E642C)
        j       damage_processed_
        nop
        _return:
        OS.patch_end()

        lw      a0, 0x00A0(sp)              // a0 = fighter object
        addiu   sp, sp, -0x18
        sw      t6, 0x0010(sp)              // save t6 (damage lag, used after this)
        jal     on_damage_
        nop
        lw      t6, 0x0010(sp)
        addiu   sp, sp, 0x18

        addiu   t7, r0, 0x0001              // original line 1
        j       _return
        sw      t7, 0x0084(sp)              // original line 2
    }

    // @ Description
    // Decides what happens to a fighter that just took damage
    // @ Arguments
    // a0 - fighter object
    scope on_damage_: {
        addiu   sp, sp, -0x30
        sw      ra, 0x0014(sp)
        sw      s0, 0x0018(sp)
        sw      s1, 0x001C(sp)
        sw      s2, 0x0020(sp)

        or      s0, a0, r0                  // s0 = fighter object
        lw      s1, 0x0084(a0)              // s1 = fighter struct
        jal     get_entry_
        or      a0, s1, r0
        or      s2, v0, r0                  // s2 = entry (may be 0)
        beqz    s2, _end                    // skip if no entry available
        nop

        // track when this hit's hitstun ends, keeping when the previous one did (for the freeze meter)
        lw      t0, 0x0008(s2)              // t0 = frame the previous hitstun ends
        sw      t0, 0x0024(sp)              // save it
        OS.read_word(Global.current_screen_frame_count, t9) // t9 = current frame
        lhu     t1, 0x0B1A(s1)              // t1 = hitstun
        addu    t1, t1, t9
        sw      t1, 0x0008(s2)              // frame this hit's hitstun ends

        lw      t0, 0x09D4(s1)              // t0 = update routine
        li      t1, frozen_update_
        bne     t0, t1, _check_pending      // if the hit didn't keep the fighter frozen, check if it was frozen
        nop

        // still frozen (the hit didn't change the fighter's status)
        or      a0, s1, r0
        lw      a1, 0x026C(s1)              // a1 = remaining freeze time
        jal     frozen_hit_time_
        or      a2, s2, r0
        or      t2, v0, r0                  // t2 = new freeze time
        bgtz    t2, _hit_sound              // if there's time left, stay frozen
        sw      t2, 0x026C(s1)
        jal     shatter_
        or      a0, s0, r0
        jal     thaw_
        or      a0, s0, r0
        b       _end
        nop

        _hit_sound:
        jal     0x800269C0                  // play FGM
        lli     a0, ICE_FGM
        b       _end
        nop

        _check_pending:
        lw      a1, 0x0010(s2)              // a1 = pending freeze time
        beqz    a1, _check_freeze           // if the fighter wasn't frozen, check if they freeze
        sw      r0, 0x0010(s2)              // clear pending freeze time

        // the hit took the fighter out of the frozen state: refreeze them with the new time
        or      a0, s1, r0
        jal     frozen_hit_time_
        or      a2, s2, r0
        or      s2, v0, r0                  // s2 = new freeze time
        blez    s2, _shatter                // if no time left, break out
        nop

        // quarter knockback
        li      at, KNOCKBACK_MUL
        mtc1    at, f2
        lwc1    f4, 0x0054(s1)
        mul.s   f4, f4, f2
        swc1    f4, 0x0054(s1)              // knockback velocity x *= 0.25
        lwc1    f4, 0x0058(s1)
        mul.s   f4, f4, f2
        swc1    f4, 0x0058(s1)              // knockback velocity y *= 0.25
        lwc1    f4, 0x006C(s1)
        mul.s   f4, f4, f2
        swc1    f4, 0x006C(s1)              // ground knockback velocity *= 0.25

        or      a0, s0, r0
        jal     enter_
        or      a1, s2, r0                  // a1 = freeze time
        b       _end
        nop

        _shatter:
        jal     shatter_
        or      a0, s0, r0
        b       _end
        nop

        _check_freeze:
        lw      t0, 0x07F8(s1)              // t0 = damage type
        lli     at, ELEMENT
        bne     t0, at, _end                // if not a freezing hit, do nothing
        nop

        // grabbed or trapped fighters can't be frozen, whether they still are...
        is_captured(s1, t0)
        bnez    t0, _end
        nop
        // ...or this hit just took them out of it
        OS.read_word(Global.current_screen_frame_count, t9) // t9 = current frame
        lw      t0, 0x0014(s2)              // t0 = frame of last hit while captured
        beq     t0, t9, _end
        nop

        if DEBUG_ALWAYS_FREEZE == 1 {
        or      a0, s1, r0
        lw      a1, 0x07F0(s1)              // a1 = damage
        jal     get_freeze_time_
        or      a2, a1, r0                  // a2 = damage added to the percent
        sw      v0, 0x0010(sp)              // save freeze time
        b       _freeze_debug
        nop
        }

        OS.read_word(Global.current_screen_frame_count, t9) // t9 = current frame

        // can't be frozen again right after thawing
        lw      t0, 0x000C(s2)              // t0 = last thaw frame
        beqz    t0, _meter                  // skip if never thawed
        subu    t0, t9, t0                  // t0 = frames since thawing
        sltiu   at, t0, IMMUNE_TIME
        bnez    at, _end                    // if immune, do nothing
        nop

        _meter:
        or      a0, s1, r0
        lw      a1, 0x07F0(s1)              // a1 = damage
        jal     get_freeze_time_
        or      a2, a1, r0                  // a2 = damage added to the percent
        sw      v0, 0x0010(sp)              // save freeze time

        // freeze time adds up while the fighter stays in hitstun
        OS.read_word(Global.current_screen_frame_count, t9) // t9 = current frame
        lw      t0, 0x0024(sp)              // t0 = frame the previous hitstun ends
        lw      t1, 0x0004(s2)              // t1 = meter
        sltu    at, t0, t9                  // at = 1 if the previous hitstun ended before this hit
        bnezl   at, pc() + 8                // if the fighter wasn't in hitstun...
        or      t1, r0, r0                  // ...reset the meter
        addu    t1, t1, v0                  // meter += freeze time
        sw      t1, 0x0004(s2)              // save meter

        // freeze if knockback is high enough...
        li      at, KNOCKBACK_MIN
        mtc1    at, f2                      // f2 = KNOCKBACK_MIN
        lwc1    f4, 0x07E0(s1)              // f4 = knockback
        c.le.s  f2, f4                      // KNOCKBACK_MIN <= knockback?
        nop
        bc1t    _freeze
        nop
        // ...or if the meter is full
        sltiu   at, t1, METER_THRESHOLD + 1
        bnez    at, _end                    // if meter <= METER_THRESHOLD, don't freeze
        nop

        _freeze:
        sw      r0, 0x0004(s2)              // meter = 0
        _freeze_debug:
        sw      r0, 0x0018(s2)              // hits in current freeze = 0
        sw      r0, 0x001C(s2)              // leftover mash time = 0
        sw      r0, 0x0020(s2)              // spin angle = 0
        li      t0, NO_SPIN
        sw      t0, 0x0030(s2)              // read the pose on the next spin
        lw      a1, 0x0010(sp)              // a1 = freeze time
        sltiu   at, a1, MAX_TIME + 1
        beqzl   at, pc() + 8                // if over the limit...
        lli     a1, MAX_TIME                // ...use the limit
        jal     enter_
        or      a0, s0, r0

        _end:
        lw      ra, 0x0014(sp)
        lw      s0, 0x0018(sp)
        lw      s1, 0x001C(sp)
        lw      s2, 0x0020(sp)
        jr      ra
        addiu   sp, sp, 0x30
    }

    // @ Description
    // Puts a fighter in the frozen state
    // @ Arguments
    // a0 - fighter object
    // a1 - freeze time
    scope enter_: {
        addiu   sp, sp, -0x30
        sw      ra, 0x0014(sp)
        sw      s0, 0x0018(sp)
        sw      s1, 0x001C(sp)
        sw      a1, 0x0020(sp)              // save freeze time

        or      s0, a0, r0                  // s0 = fighter object
        lw      s1, 0x0084(a0)              // s1 = fighter struct

        lli     a1, MAX_TIME
        lw      t0, 0x0020(sp)
        sltu    at, a1, t0                  // at = 1 if freeze time > MAX_TIME
        bnezl   at, pc() + 8
        sw      a1, 0x0020(sp)              // freeze time = MAX_TIME

        // frozen pose
        lli     a1, ACTION                  // a1 = action
        or      a2, r0, r0                  // a2 = starting frame = 0
        sw      r0, 0x0010(sp)              // argument 4 = 0
        jal     0x800E6F24                  // ftMainSetStatus
        or      a3, r0, r0                  // a3 = animation speed = 0

        li      t0, frozen_update_
        sw      t0, 0x09D4(s1)              // update
        sw      r0, 0x09D8(s1)              // accessory
        sw      r0, 0x09DC(s1)              // interrupt
        li      t0, frozen_physics_
        sw      t0, 0x09E0(s1)              // physics
        li      t0, 0x8014CD24              // ftCommonYoshiEggProcMap
        sw      t0, 0x09E4(s1)              // map collision

        // can't be pushed by other fighters (jostle_skip_frozen_ handles pushing others)
        lbu     t0, 0x018F(s1)
        ori     t0, t0, 0x0008
        sb      t0, 0x018F(s1)              // is_jostle_ignore = 1 (cleared on status change)

        // freeze time is kept in breakout_wait, which mashing decreases
        or      a0, s1, r0
        jal     0x8014E3EC                  // ftCommonCaptureTrappedInitBreakoutVars(fp, breakout_wait)
        lw      a1, 0x0020(sp)              // a1 = freeze time


        or      a0, s1, r0
        jal     0x800E8098                  // ftParamSetCaptureImmuneMask(fp, mask)
        lli     a1, 0x003F                  // a1 = can't be grabbed by anything

        jal     make_effect_
        or      a0, s0, r0

        jal     apply_spin_                 // keep the spin from a previous freeze, during hitlag too
        or      a0, s0, r0

        jal     0x800269C0                  // play FGM
        lli     a0, ICE_FGM

        lw      ra, 0x0014(sp)
        lw      s0, 0x0018(sp)
        lw      s1, 0x001C(sp)
        jr      ra
        addiu   sp, sp, 0x30
    }

    // @ Description
    // Creates the ice block around a fighter
    // @ Arguments
    // a0 - fighter object
    scope make_effect_: {
        addiu   sp, sp, -0x40
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)

        // point the block display list to the texture. Looked up instead of using TEXTURES.FILE.ptr, which
        // could be left over from a previous battle in scenes that don't load extra imports' files.
        jal     0x800CD698                  // v0 = lbRelocFindForceStatusBufferFile(texture file)
        lli     a0, TEXTURES.FILE.id
        beqz    v0, _end                    // skip if the texture file isn't loaded
        addiu   v0, v0, TEXTURES.ICE_BLOCK.OFFSET
        li      at, block_dl_texture
        sw      v0, 0x0004(at)              // texture image = file + offset

        li      a0, effect_desc
        jal     0x800FDB1C                  // efManagerMakeEffectForce
        nop
        beqz    v0, _end                    // skip if no effect was created
        lw      a0, 0x0018(sp)              // a0 = fighter object

        lw      t0, 0x0084(v0)              // t0 = effect struct
        sw      a0, 0x0004(t0)              // effect fighter = this fighter
        lw      t1, 0x0084(a0)              // t1 = fighter struct
        lw      t2, 0x0074(v0)              // t2 = effect main DObj
        lw      t3, 0x08E8(t1)              // t3 = fighter TopN joint
        sw      t3, 0x0084(t2)              // follow the fighter

        // scale to cover the fighter: side = BLOCK_SIZE_MUL * max(ECB height, ECB width) * root bone scale.
        // ECB attributes are for the normal size, so the root bone (TopN) scale covers giants (except Giga Bowser).
        lw      t4, 0x09C8(t1)              // t4 = fighter attributes
        lwc1    f2, 0x009C(t4)              // f2 = ECB top
        lwc1    f6, 0x00A4(t4)              // f6 = ECB bottom
        sub.s   f2, f2, f6                  // f2 = ECB height
        lwc1    f6, 0x00A8(t4)              // f6 = ECB half width
        add.s   f6, f6, f6                  // f6 = ECB width
        c.lt.s  f2, f6
        nop
        bc1tl   pc() + 8
        mov.s   f2, f6                      // f2 = max(height, width)
        lw      t5, 0x0008(t1)              // t5 = character id
        lli     at, Character.id.GBOWSER
        beq     t5, at, _scaled             // Giga Bowser's ECB is already sized for his model: skip the root scale
        lwc1    f6, 0x0040(t3)              // f6 = root bone x scale
        mul.s   f2, f2, f6
        _scaled:
        li      at, BLOCK_SIZE_MUL
        mtc1    at, f4
        mul.s   f2, f2, f4                  // f2 = block side
        li      at, BLOCK_QUAD_SIZE
        mtc1    at, f4
        div.s   f4, f2, f4                  // f4 = scale
        swc1    f4, 0x0040(t2)              // scale x
        swc1    f4, 0x0044(t2)              // scale y
        swc1    f4, 0x0048(t2)              // scale z

        // the first hurtbox becomes a cube that fills the block, so hits on the ice connect. Hurtbox sizes are in the
        // joint's space (multiplied by the model scale), so divide by the root scale. The game restores the default
        // hurtboxes on the next status change since is_damage_coll_modify is set.
        lli     at, BLOCK_HALF_SIZE
        mtc1    at, f6
        cvt.s.w f6, f6
        mul.s   f6, f6, f4                  // f6 = block half side
        li      at, HURTBOX_MUL
        mtc1    at, f8
        mul.s   f6, f6, f8
        lwc1    f8, 0x0040(t3)              // f8 = root bone x scale
        div.s   f6, f6, f8                  // f6 = hurtbox half side in joint space
        lli     t6, 0x0004
        sw      t6, 0x05C0(t1)              // joint id = 4 (first bone)
        lw      t6, 0x08F8(t1)
        sw      t6, 0x05C4(t1)              // joint = joints[4]
        sw      r0, 0x05D0(t1)
        sw      r0, 0x05D4(t1)
        sw      r0, 0x05D8(t1)              // offset = 0
        swc1    f6, 0x05DC(t1)
        swc1    f6, 0x05E0(t1)
        swc1    f6, 0x05E4(t1)              // size
        lbu     t6, 0x018C(t1)
        ori     t6, t6, 0x0020
        sb      t6, 0x018C(t1)              // is_damage_coll_modify = TRUE

        // the block is centered on the fighter's body in effect_display_
        lw      t5, 0x0010(t2)              // t5 = block quad DObj
        beqz    t5, _attach
        nop
        sw      r0, 0x0024(t5)              // z = 0

        _attach:
        // the effect is removed when the fighter changes status
        lbu     t0, 0x018F(t1)
        ori     t0, t0, 0x0010
        sb      t0, 0x018F(t1)              // is_effect_attach = TRUE

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x40
    }

    // @ Description
    // Ice block effect update. It follows the fighter on its own, so there's nothing to do.
    scope effect_update_: {
        jr      ra
        nop
    }

    // @ Description
    // Ice block effect display. Shakes the block along with the fighter when they're in hitlag (shuffle), which
    // fighters apply at display time, then draws it.
    // @ Arguments
    // a0 - effect object
    scope effect_display_: {
        addiu   sp, sp, -0x48
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)

        lw      t0, 0x0084(a0)              // t0 = effect struct
        lw      t0, 0x0004(t0)              // t0 = fighter object
        lw      t0, 0x0084(t0)              // t0 = fighter struct
        sw      t0, 0x0020(sp)              // save fighter struct
        lw      t1, 0x0074(a0)              // t1 = effect main DObj
        lw      t2, 0x0010(t1)              // t2 = block quad DObj
        beqz    t2, _draw
        nop
        sw      t2, 0x001C(sp)              // save block quad DObj
        lwc1    f4, 0x0040(t1)              // f4 = block scale
        swc1    f4, 0x0024(sp)              // save block scale

        mtc1    r0, f6                      // f6 = shuffle x = 0
        mtc1    r0, f8                      // f8 = shuffle y = 0
        lhu     t3, 0x0276(t0)              // t3 = shuffle timer
        beqz    t3, _set_position           // skip if not shuffling
        nop
        lbu     t3, 0x0274(t0)              // t3 = 1 if electric shuffle
        sll     t3, t3, 5                   // t3 = offset to shuffle positions for this kind (4 Vec2f)
        lbu     t4, 0x0272(t0)              // t4 = shuffle frame index
        sll     t4, t4, 3                   // t4 = offset to this frame's position
        addu    t3, t3, t4
        li      t4, SHUFFLE_POSITIONS
        addu    t3, t3, t4                  // t3 = shuffle position
        lwc1    f6, 0x0000(t3)              // f6 = shuffle x
        lwc1    f8, 0x0004(t3)              // f8 = shuffle y

        _set_position:
        swc1    f6, 0x0028(sp)              // save shuffle x
        swc1    f8, 0x002C(sp)              // save shuffle y

        // center on the fighter's first bone (joints[4]), which spin_ rotates
        sw      r0, 0x0030(sp)
        sw      r0, 0x0034(sp)
        sw      r0, 0x0038(sp)              // offset from the joint = 0
        lw      a0, 0x08F8(t0)              // a0 = first bone (joints[4])
        jal     0x800EDF24                  // gmCollisionGetFighterPartsWorldPosition(joint, &pos)
        addiu   a1, sp, 0x0030              // a1 = pos

        lw      t0, 0x0020(sp)              // t0 = fighter struct
        lw      t1, 0x0078(t0)              // t1 = fighter position (followed by the block)
        lwc1    f6, 0x0030(sp)
        lwc1    f2, 0x0000(t1)
        sub.s   f6, f6, f2                  // f6 = joint x - fighter x
        lwc1    f8, 0x0034(sp)
        lwc1    f2, 0x0004(t1)
        sub.s   f8, f8, f2                  // f8 = joint y - fighter y

        lwc1    f2, 0x0028(sp)
        add.s   f6, f6, f2                  // + shuffle x
        lwc1    f2, 0x002C(sp)
        add.s   f8, f8, f2                  // + shuffle y
        lwc1    f4, 0x0024(sp)              // f4 = block scale
        div.s   f6, f6, f4
        div.s   f8, f8, f4                  // in block space
        lw      t2, 0x001C(sp)              // t2 = block quad DObj
        swc1    f6, 0x001C(t2)              // x
        swc1    f8, 0x0020(t2)              // y

        // spin with the fighter (see spin_): the billboard's z rotation is counterclockwise on screen
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        or      a0, t0, r0
        lw      t2, 0x001C(sp)
        mtc1    r0, f2                      // f2 = 0 if there's no entry
        bnezl   v0, pc() + 8
        lwc1    f2, 0x0020(v0)              // f2 = spin angle (clockwise)
        neg.s   f2, f2
        swc1    f2, 0x0038(t2)              // z rotation

        _draw:
        jal     0x80014768                  // gcDrawDObjTreeDLLinksForGObj(gobj)
        lw      a0, 0x0018(sp)

        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x48
    }

    // @ Description
    // Update routine while frozen: counts down the freeze time, which mashing shortens like Ultimate
    // (MASH_STICK_TENTHS per stick input, MASH_BUTTON_TENTHS per button input)
    // @ Arguments
    // a0 - fighter object
    scope frozen_update_: {
        addiu   sp, sp, -0x28
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        lw      t0, 0x026C(a0)              // t0 = freeze time
        sw      t0, 0x001C(sp)              // save freeze time
        lhu     t0, 0x0270(a0)              // t0 = breakout_lr and breakout_ud (stick mash directions)
        jal     0x8014E400                  // ftCommonCaptureTrappedUpdateBreakoutVars(fp) - breakout_wait -1 per button and per stick input
        sw      t0, 0x0020(sp)              // save stick mash directions

        lw      a0, 0x0018(sp)
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        lw      a0, 0x0084(a0)              // a0 = fighter struct

        lw      t9, 0x0018(sp)
        lw      t9, 0x0084(t9)              // t9 = fighter struct
        lw      t0, 0x001C(sp)              // t0 = freeze time before mashing
        lw      t1, 0x026C(t9)              // t1 = freeze time after mashing
        subu    t2, t0, t1                  // t2 = mash inputs
        lhu     t3, 0x0270(t9)              // t3 = stick mash directions now
        lw      t4, 0x0020(sp)              // t4 = stick mash directions before
        xor     t3, t3, t4
        sltu    t3, r0, t3                  // t3 = 1 if the stick mash direction changed (stick input)
        subu    t2, t2, t3                  // t2 = button inputs
        lli     at, MASH_STICK_TENTHS
        multu   t3, at
        mflo    t3
        lli     at, MASH_BUTTON_TENTHS
        multu   t2, at
        mflo    t2
        addu    t3, t3, t2                  // t3 = time removed by mashing, in tenths of a frame
        beqz    v0, _divide                 // skip leftover if no entry
        nop
        lw      t2, 0x001C(v0)              // t2 = leftover mash time
        addu    t3, t3, t2
        _divide:
        lli     at, 10
        divu    t3, at
        mflo    t2                          // t2 = frames removed by mashing
        mfhi    t3                          // t3 = leftover, in tenths of a frame
        bnezl   v0, pc() + 8
        sw      t3, 0x001C(v0)              // save leftover mash time

        subu    t0, t0, t2
        addiu   t0, t0, -0x0001             // t0 = freeze time - 1 - mashing
        bgtz    t0, _end                    // if there's time left, stay frozen
        sw      t0, 0x026C(t9)              // save freeze time

        lw      a0, 0x0018(sp)
        jal     shatter_
        nop
        lw      a0, 0x0018(sp)
        jal     thaw_
        nop

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x28
    }

    // @ Description
    // Physics routine while frozen: no control, slower falling and knockback that decays slower
    // @ Arguments
    // a0 - fighter object
    scope frozen_physics_: {
        addiu   sp, sp, -0x20
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)
        lw      t0, 0x0084(a0)              // t0 = fighter struct
        lw      t1, 0x014C(t0)              // t1 = kinetic state
        bnez    t1, _air
        nop

        _ground:
        jal     0x800D8BB4                  // ftPhysicsApplyGroundVelFriction(fighter_gobj)
        nop
        b       _end
        nop

        _air:
        sw      r0, 0x0048(t0)              // no air drift
        lw      t1, 0x09C8(t0)              // t1 = attributes
        lw      a1, 0x0058(t1)              // a1 = gravity
        lwc1    f2, 0x005C(t1)              // f2 = terminal velocity
        li      at, TVEL_MUL
        mtc1    at, f4
        mul.s   f2, f2, f4
        mfc1    a2, f2                      // a2 = terminal velocity * TVEL_MUL
        jal     0x800D8D68                  // ftPhysicsApplyGravityClampTVel(fp, gravity, tvel)
        or      a0, t0, r0

        // knockback decays by 1.7 per frame elsewhere; give some of it back
        lw      a0, 0x0018(sp)
        lw      t0, 0x0084(a0)              // t0 = fighter struct
        lwc1    f2, 0x0054(t0)              // f2 = knockback velocity x
        lwc1    f4, 0x0058(t0)              // f4 = knockback velocity y
        mul.s   f6, f2, f2
        mul.s   f8, f4, f4
        add.s   f6, f6, f8
        sqrt.s  f6, f6                      // f6 = knockback speed
        mtc1    r0, f8
        c.eq.s  f6, f8
        nop
        bc1t    _end                        // skip if no knockback
        nop
        li      at, DECAY_COMPENSATION
        mtc1    at, f8
        div.s   f8, f8, f6                  // f8 = compensation / speed
        mul.s   f10, f2, f8
        add.s   f2, f2, f10
        swc1    f2, 0x0054(t0)              // knockback velocity x
        mul.s   f10, f4, f8
        add.s   f4, f4, f10
        swc1    f4, 0x0058(t0)              // knockback velocity y

        _end:
        jal     spin_
        lw      a0, 0x0018(sp)              // a0 = fighter object

        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x20
    }

    // @ Description
    // Frozen fighters don't push other fighters either: treat them as airborne in the jostle check
    // (ftMainProcUpdateInterrupt, which also has DualFighter's skip_sister_jostle hook just before this)
    scope jostle_skip_frozen_: {
        OS.patch_start(0x5D28C, 0x800E1A8C)
        j       jostle_skip_frozen_
        nop
        OS.patch_end()

        // v0 = other fighter struct, a1 = other fighter object
        lw      t7, 0x014C(v0)              // original line 1: t7 = other fighter kinetic state
        bnez    t7, _skip                   // original line 2: skip if airborne
        lw      t7, 0x09D4(v0)              // t7 = other fighter update routine
        li      t8, frozen_update_
        beq     t7, t8, _skip               // skip if frozen
        nop
        j       0x800E1A98                  // continue jostle check
        nop

        _skip:
        j       0x800E1C70                  // next fighter
        lw      a1, 0x0004(a1)              // original delay slot: a1 = next fighter object
    }

    // @ Description
    // Stores sinf and cosf of the f32 at angle(sp) in sin(sp) and cos(sp)
    macro sincos(angle, sin, cos) {
        jal     0x800303F0                  // sinf
        lwc1    f12, {angle}(sp)
        swc1    f0, {sin}(sp)
        jal     0x80035CD0                  // cosf
        lwc1    f12, {angle}(sp)
        swc1    f0, {cos}(sp)
    }

    // @ Description
    // Spins a frozen fighter with their movement speed, counterclockwise when facing right, clockwise when facing left.
    // The model rotates like when tumbling (DamageFlyRoll); the ice block copies it in effect_display_.
    // @ Arguments
    // a0 - fighter object
    scope spin_: {
        addiu   sp, sp, -0x78
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        beqz    v0, _end
        nop
        sw      v0, 0x0020(sp)              // save entry

        lw      t0, 0x0018(sp)
        lw      t0, 0x0084(t0)              // t0 = fighter struct
        lwc1    f2, 0x0048(t0)
        lwc1    f4, 0x0054(t0)
        add.s   f2, f2, f4                  // f2 = velocity x (own + knockback)
        lwc1    f4, 0x004C(t0)
        lwc1    f6, 0x0058(t0)
        add.s   f4, f4, f6                  // f4 = velocity y (own + knockback)
        mul.s   f6, f2, f2
        mul.s   f8, f4, f4
        add.s   f6, f6, f8
        sqrt.s  f6, f6                      // f6 = speed
        li      at, SPIN_MUL
        mtc1    at, f8
        mul.s   f6, f6, f8                  // f6 = spin this frame
        lw      t1, 0x0044(t0)              // t1 = facing direction (1 or -1)
        bgtzl   t1, pc() + 8                // if facing right...
        neg.s   f6, f6                      // ...spin counterclockwise
        lwc1    f8, 0x0020(v0)
        add.s   f8, f8, f6
        swc1    f8, 0x0020(v0)              // spin angle += spin this frame

        jal     apply_spin_
        lw      a0, 0x0018(sp)              // a0 = fighter object

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x78
    }

    // @ Description
    // Rotates a frozen fighter's model by their spin angle (see spin_). Also used when they get frozen, since
    // physics (and so spin_) doesn't run during hitlag.
    // @ Arguments
    // a0 - fighter object
    scope apply_spin_: {
        addiu   sp, sp, -0x78
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        beqz    v0, _end
        nop
        sw      v0, 0x0020(sp)              // save entry
        lw      t0, 0x0018(sp)
        lw      t0, 0x0084(t0)              // t0 = fighter struct
        lw      t1, 0x0044(t0)              // t1 = facing direction (1 or -1)
        lwc1    f8, 0x0020(v0)              // f8 = spin angle

        lw      a0, 0x08F8(t0)              // a0 = model root joint (joints[4])
        beqz    a0, _end
        nop
        sw      a0, 0x001C(sp)              // save joint
        mtc1    t1, f10
        cvt.s.w f10, f10
        mul.s   f8, f8, f10                 // the model's x rotation is mirrored when facing left
        swc1    f8, 0x0030(sp)              // t = spin rotation, around the joint's parent x axis

        // if the joint's rotation isn't what we wrote last, the animation set a new pose: keep it
        lw      t2, 0x0030(a0)
        lw      t3, 0x0030(v0)
        bne     t2, t3, _read_pose
        lw      t2, 0x0034(a0)
        lw      t3, 0x0034(v0)
        bne     t2, t3, _read_pose
        lw      t2, 0x0038(a0)
        lw      t3, 0x0038(v0)
        beq     t2, t3, _rotate
        nop
        _read_pose:
        lw      t2, 0x0030(a0)
        sw      t2, 0x0024(v0)
        lw      t2, 0x0034(a0)
        sw      t2, 0x0028(v0)
        lw      t2, 0x0038(a0)
        sw      t2, 0x002C(v0)              // pose = joint rotation

        _rotate:
        // the joint's rotation is Rz(c) * Ry(b) * Rx(a), so x applies first, around the posed body's own axis.
        // Instead, rotate the posed joint around its parent's x axis: R = Rx(t) * Rz(c) * Ry(b) * Rx(a),
        // then turn R back into x, y, z angles: x = atan2(R21, R22), y = asin(-R20), z = atan2(R10, R00)
        lw      t2, 0x0024(v0)
        sw      t2, 0x0024(sp)              // a = pose rotation x
        lw      t2, 0x0028(v0)
        sw      t2, 0x0028(sp)              // b = pose rotation y
        lw      t2, 0x002C(v0)
        sw      t2, 0x002C(sp)              // c = pose rotation z

        sincos(0x24, 0x34, 0x38)            // sa, ca
        sincos(0x28, 0x3C, 0x40)            // sb, cb
        sincos(0x2C, 0x44, 0x48)            // sc, cc
        sincos(0x30, 0x4C, 0x50)            // st, ct

        lwc1    f2, 0x0034(sp)              // f2 = sa
        lwc1    f4, 0x0038(sp)              // f4 = ca
        lwc1    f6, 0x003C(sp)              // f6 = sb
        lwc1    f8, 0x0040(sp)              // f8 = cb
        lwc1    f10, 0x0044(sp)             // f10 = sc
        lwc1    f16, 0x0048(sp)             // f16 = cc
        lwc1    f18, 0x004C(sp)             // f18 = st
        lwc1    f12, 0x0050(sp)             // f12 = ct

        swc1    f20, 0x0070(sp)             // save f20 (callee-saved)
        mul.s   f0, f8, f16
        swc1    f0, 0x0054(sp)              // R00 = cb * cc
        mul.s   f14, f8, f10                // f14 = cb * sc (pose R10; pose R20 = -sb)
        mul.s   f0, f12, f14
        mul.s   f20, f18, f6
        add.s   f0, f0, f20
        swc1    f0, 0x0058(sp)              // R10 = ct * cb * sc + st * sb
        mul.s   f0, f18, f14
        mul.s   f20, f12, f6
        sub.s   f0, f0, f20
        swc1    f0, 0x005C(sp)              // R20 = st * cb * sc - ct * sb

        mul.s   f14, f6, f10                // f14 = sb * sc
        mul.s   f0, f2, f14
        mul.s   f20, f4, f16
        add.s   f0, f0, f20                 // f0 = pose R11 = sa * sb * sc + ca * cc
        mul.s   f0, f18, f0
        mul.s   f20, f2, f8                 // f20 = pose R21 = sa * cb
        mul.s   f20, f12, f20
        add.s   f0, f0, f20
        swc1    f0, 0x0060(sp)              // R21 = st * pose R11 + ct * pose R21
        mul.s   f0, f4, f14
        mul.s   f20, f2, f16
        sub.s   f0, f0, f20                 // f0 = pose R12 = ca * sb * sc - sa * cc
        mul.s   f0, f18, f0
        mul.s   f20, f4, f8                 // f20 = pose R22 = ca * cb
        mul.s   f20, f12, f20
        add.s   f0, f0, f20
        swc1    f0, 0x0064(sp)              // R22 = st * pose R12 + ct * pose R22
        lwc1    f20, 0x0070(sp)             // restore f20

        lwc1    f12, 0x0060(sp)
        jal     0x8001863C                  // syUtilsArcTan2(y, x)
        lwc1    f14, 0x0064(sp)
        swc1    f0, 0x0068(sp)              // rotation x = atan2(R21, R22)
        lwc1    f12, 0x005C(sp)
        jal     0x8001873C                  // syUtilsArcSin(x) (clamps to +-1)
        neg.s   f12, f12
        swc1    f0, 0x006C(sp)              // rotation y = asin(-R20)
        lwc1    f12, 0x0058(sp)
        jal     0x8001863C                  // syUtilsArcTan2(y, x)
        lwc1    f14, 0x0054(sp)             // f0 = rotation z = atan2(R10, R00)

        lw      a0, 0x001C(sp)              // a0 = joint
        lw      v0, 0x0020(sp)              // v0 = entry
        lw      t2, 0x0068(sp)
        sw      t2, 0x0030(a0)
        sw      t2, 0x0030(v0)              // rotation x
        lw      t2, 0x006C(sp)
        sw      t2, 0x0034(a0)
        sw      t2, 0x0034(v0)              // rotation y
        swc1    f0, 0x0038(a0)
        jal     0x800EB528                  // ftParamsUpdateFighterPartsTransformAll(joint)
        swc1    f0, 0x0038(v0)              // rotation z

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x78
    }

    // @ Description
    // Undoes the spin of a fighter's model
    // @ Arguments
    // a0 - fighter struct
    scope reset_spin_: {
        lw      t3, 0x08F8(a0)              // t3 = model root joint (joints[4])
        beqz    t3, _end
        nop
        addiu   sp, sp, -0x18
        sw      ra, 0x0014(sp)
        jal     get_entry_                  // v0 = entry (only uses t0-t2)
        nop
        or      a0, t3, r0                  // a0 = joint
        beqz    v0, _update
        sw      r0, 0x0030(a0)              // rotation x = 0 if there's no entry
        lw      t2, 0x0024(v0)
        sw      t2, 0x0030(a0)
        lw      t2, 0x0028(v0)
        sw      t2, 0x0034(a0)
        lw      t2, 0x002C(v0)
        sw      t2, 0x0038(a0)              // rotation = pose
        li      t2, NO_SPIN
        sw      t2, 0x0030(v0)              // read the pose on the next spin
        _update:
        jal     0x800EB528                  // ftParamsUpdateFighterPartsTransformAll(joint)
        nop
        lw      ra, 0x0014(sp)
        addiu   sp, sp, 0x18
        _end:
        jr      ra
        nop
    }

    // @ Description
    // Shatters the ice around a fighter and makes them immune to freezing for a while
    // @ Arguments
    // a0 - fighter object
    scope shatter_: {
        addiu   sp, sp, -0x30
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)

        jal     reset_spin_
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        lw      a0, 0x0018(sp)

        lw      t0, 0x0084(a0)              // t0 = fighter struct
        sw      r0, 0x0020(sp)
        sw      r0, 0x0024(sp)
        sw      r0, 0x0028(sp)              // offset from the joint = 0
        lw      a0, 0x08F8(t0)              // a0 = first bone (joints[4]), where the ice block is
        jal     0x800EDF24                  // gmCollisionGetFighterPartsWorldPosition(joint, &pos)
        addiu   a1, sp, 0x0020
        sw      r0, 0x0028(sp)              // pos z = 0
        jal     0x801041A0                  // efManagerEggBreakMakeEffect(Vec3f *pos)
        addiu   a0, sp, 0x0020
        beqz    v0, _sound                  // skip if no particle was created
        nop
        // tint it blue, keeping alpha. The shards are child particles made on its first frame, which share its
        // generator id, so tint every particle in its list with that id.
        lhu     t2, 0x0004(v0)              // t2 = generator id
        lbu     t3, 0x0008(v0)              // t3 = bank id
        srl     t3, t3, 3
        sll     t3, t3, 2
        li      t4, 0x800D6358              // sLBParticleStructsAllocLinks
        addu    t4, t4, t3
        lw      t4, 0x0000(t4)              // t4 = first particle in the list
        li      t5, SHATTER_PRIM_COLOR
        li      t6, SHATTER_ENV_COLOR

        _tint_loop:
        beqz    t4, _sound
        nop
        lhu     t3, 0x0004(t4)
        bne     t3, t2, _tint_next          // skip particles from other effects
        srl     t1, t5, 16
        sb      t1, 0x0048(t4)              // prim color R
        sb      t1, 0x004C(t4)              // target prim color R
        srl     t1, t5, 8
        sb      t1, 0x0049(t4)              // prim color G
        sb      t1, 0x004D(t4)              // target prim color G
        sb      t5, 0x004A(t4)              // prim color B
        sb      t5, 0x004E(t4)              // target prim color B
        srl     t1, t6, 16
        sb      t1, 0x0050(t4)              // env color R
        sb      t1, 0x0054(t4)              // target env color R
        srl     t1, t6, 8
        sb      t1, 0x0051(t4)              // env color G
        sb      t1, 0x0055(t4)              // target env color G
        sb      t6, 0x0052(t4)              // env color B
        sb      t6, 0x0056(t4)              // target env color B
        _tint_next:
        b       _tint_loop
        lw      t4, 0x0000(t4)              // next particle

        _sound:

        jal     0x800269C0                  // play FGM
        lli     a0, ICE_FGM

        lw      a0, 0x0018(sp)
        jal     get_entry_
        lw      a0, 0x0084(a0)              // a0 = fighter struct
        beqz    v0, _end
        nop
        OS.read_word(Global.current_screen_frame_count, t9) // t9 = current frame
        sw      t9, 0x000C(v0)              // last thaw frame = now

        _end:
        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x30
    }

    // @ Description
    // Breaks a fighter out of the frozen state: they pop up, losing their knockback
    // @ Arguments
    // a0 - fighter object
    scope thaw_: {
        addiu   sp, sp, -0x20
        sw      ra, 0x0014(sp)
        sw      a0, 0x0018(sp)

        lw      t0, 0x0084(a0)              // t0 = fighter struct
        sw      r0, 0x0054(t0)              // knockback velocity x = 0
        sw      r0, 0x0058(t0)              // knockback velocity y = 0
        sw      r0, 0x006C(t0)              // ground knockback velocity = 0
        sw      r0, 0x0048(t0)              // velocity x = 0
        li      at, ESCAPE_VEL_Y
        sw      at, 0x004C(t0)              // velocity y = ESCAPE_VEL_Y

        jal     0x800DEEC8                  // mpCommonSetFighterAir(fp)
        or      a0, t0, r0
        jal     0x8013F9E0                  // ftCommonFallSetStatus(fighter_gobj)
        lw      a0, 0x0018(sp)

        lw      ra, 0x0014(sp)
        jr      ra
        addiu   sp, sp, 0x20
    }
}

} // __FREEZE__
