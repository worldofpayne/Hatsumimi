--[[
Hatsumimi pause panel for mpv.

Watch with subtitles hidden. Pause, and the video slides left while the last
few lines appear on the right: Japanese, with the English underneath.

While the panel is open:
  Up / Down   choose a line
  R           replay the chosen line (the panel comes back afterwards)
  M           mark the chosen line as "missed" (saved to a log for review)
  E           show / hide the English

Only does anything for videos that have a Japanese subtitle track. Everything
else plays exactly as normal. Options: script-opts/hatsumimi.conf
--]]

local mp = require "mp"
local msg = require "mp.msg"
local utils = require "mp.utils"
local options = require "mp.options"

local o = {
    lines = 5,                  -- how many past lines the panel shows
    panel_width = 0.42,         -- share of the window the panel takes
    font = "Yu Gothic UI",      -- needs Japanese glyphs (Meiryo, MS Gothic...)
    ja_size = 34,               -- text sizes, in units of a 720-high screen
    en_size = 22,
    show_english = true,        -- English visible when the panel opens
    replay_padding = 0.25,      -- seconds added before/after a replayed line
    log_path = "~~/hatsumimi-missed.jsonl",  -- ~~ = mpv's config folder
    mux_title = "Japanese (Hatsumimi)",      -- track added by fetch-subs
}
options.read_options(o, "hatsumimi")

-- ---------------------------------------------------------------------------
-- State
-- ---------------------------------------------------------------------------

local active = false        -- this file has a Japanese track: script is live
local saved = nil           -- the user's own settings, restored on exit
local ja_lines, en_lines = {}, {}   -- [start_ms] = {start, stop, text}
local marked = {}           -- [start_ms] = true, for lines marked this session

local panel_open = false
local shown = {}            -- lines currently in the panel (oldest first)
local selected = nil        -- index into `shown`
local keep_key = nil        -- start_ms of the line to reselect after a replay
local english_on = o.show_english

local replaying = false
local replay_armed = false  -- true once the replay's seek has completed
local replay_end = 0

local overlay = mp.create_osd_overlay("ass-events")

-- ---------------------------------------------------------------------------
-- Choosing tracks
-- ---------------------------------------------------------------------------

local function is_image_sub(codec)
    codec = (codec or ""):lower()
    return codec:find("pgs") or codec:find("dvd") or codec:find("dvb") or codec:find("xsub")
end

local function find_tracks()
    local ja, en
    local tracks = mp.get_property_native("track-list") or {}
    -- 1st choice: the track fetch-subs added. 2nd: any Japanese text track
    -- (that includes a separate .ja.srt file mpv picked up).
    for _, t in ipairs(tracks) do
        if t.type == "sub" and t.title == o.mux_title then ja = t end
    end
    for _, t in ipairs(tracks) do
        local lang = (t.lang or ""):lower()
        if not ja and t.type == "sub" and (lang == "jpn" or lang == "ja") and not is_image_sub(t.codec) then
            ja = t
        end
    end
    -- English: a full-dialogue text track, not "Signs & Songs" or forced.
    for _, t in ipairs(tracks) do
        local lang = (t.lang or ""):lower()
        local title = (t.title or ""):lower()
        if not en and t.type == "sub" and (lang == "eng" or lang == "en")
            and not is_image_sub(t.codec) and not t.forced
            and not (title:find("sign") or title:find("song") or title:find("forced") or title:find("karaoke")) then
            en = t
        end
    end
    return ja, en
end

-- ---------------------------------------------------------------------------
-- Text helpers
-- ---------------------------------------------------------------------------

local function esc(s)
    -- Make subtitle text safe inside ASS markup (same trick as mpv's OSC).
    s = s:gsub("\\", "\\\239\187\191")
    s = s:gsub("{", "\\{")
    s = s:gsub("}", "\\}")
    return s
end

local function utf8_chars(s)
    local t = {}
    for ch in s:gmatch("[%z\1-\127\194-\244][\128-\191]*") do t[#t + 1] = ch end
    return t
end

local function char_width(ch)
    return (#ch == 1) and 0.55 or 1.0   -- in "em": Latin ~half, kana/kanji full
end

-- libass won't wrap text positioned with \pos inside our panel, so wrap here.
local function wrap(text, max_em)
    local out = {}
    for para in (text .. "\n"):gmatch("(.-)\r?\n") do
        local line, w, last_space = {}, 0, nil
        for _, ch in ipairs(utf8_chars(para)) do
            local cw = char_width(ch)
            if w + cw > max_em and #line > 0 then
                if last_space and last_space > 1 then
                    out[#out + 1] = table.concat(line, "", 1, last_space - 1)
                    local tail = {}
                    for i = last_space + 1, #line do tail[#tail + 1] = line[i] end
                    line, w = tail, 0
                    for _, c in ipairs(line) do w = w + char_width(c) end
                else
                    out[#out + 1] = table.concat(line)
                    line, w = {}, 0
                end
                last_space = nil
            end
            if ch == " " or ch == "\227\128\128" then last_space = #line + 1 end  -- space / 全角space
            line[#line + 1] = ch
            w = w + cw
        end
        if #line > 0 then out[#out + 1] = table.concat(line) end
    end
    return out
end

local function is_sound_only(text)
    -- Lines like （すり潰す音） or ～♪: captions, not speech.
    local rest = text:gsub("（.-）", ""):gsub("%(.-%)", ""):gsub("♪", ""):gsub("～", ""):gsub("%s", "")
    return rest == ""
end

local DIM = "{\\alpha&H90&}"
local UNDIM = "{\\alpha&H00&}"

local function style_ja(visual_line)
    -- Dim a leading speaker name: （猫猫）text -> faded （猫猫）
    local s = esc(visual_line)
    s = s:gsub("^（.-）", function(name) return DIM .. name .. UNDIM end, 1)
    return s
end

-- ---------------------------------------------------------------------------
-- Recording what was heard
-- ---------------------------------------------------------------------------

local function recorder(store, start_prop, end_prop)
    return function(_, text)
        if not active or not text or text == "" then return end
        local s = mp.get_property_number(start_prop)
        if not s then return end
        local e = mp.get_property_number(end_prop) or s
        msg.debug(string.format("%s %.3f-%.3f %s", start_prop, s, e, text:gsub("\n", " / ")))
        -- Keyed by start time: replays and backward seeks overwrite instead of
        -- duplicating, and lines skipped by a forward seek are simply absent.
        store[math.floor(s * 1000 + 0.5)] = { start = s, stop = e, text = text }
    end
end

mp.observe_property("sub-text", "string", recorder(ja_lines, "sub-start", "sub-end"))
mp.observe_property("secondary-sub-text", "string",
    recorder(en_lines, "secondary-sub-start", "secondary-sub-end"))

local function recent_lines(now)
    local keys = {}
    for k, l in pairs(ja_lines) do
        if l.start <= now + 0.05 then keys[#keys + 1] = k end
    end
    table.sort(keys)
    local out = {}
    for i = math.max(1, #keys - o.lines + 1), #keys do
        local l = ja_lines[keys[i]]
        out[#out + 1] = { key = keys[i], start = l.start, stop = l.stop, text = l.text }
    end
    return out
end

local function english_for(line)
    local keys = {}
    for k, e in pairs(en_lines) do
        if e.start < line.stop - 0.1 and e.stop > line.start + 0.1 then keys[#keys + 1] = k end
    end
    table.sort(keys)
    local parts, seen = {}, {}
    for _, k in ipairs(keys) do
        local t = en_lines[k].text:gsub("\r?\n", " ")
        if not seen[t] then seen[t] = true; parts[#parts + 1] = t end
    end
    return table.concat(parts, " / ")
end

-- ---------------------------------------------------------------------------
-- Drawing the panel
-- ---------------------------------------------------------------------------

local function render()
    if not panel_open then overlay:remove(); return end
    local dim = mp.get_property_native("osd-dimensions") or {}
    local w, h = dim.w or 1280, dim.h or 720
    if w <= 0 or h <= 0 then return end
    local res_y = 720
    local res_x = math.floor(res_y * w / h)
    overlay.res_x, overlay.res_y = res_x, res_y

    local panel_x = math.floor(res_x * (1 - o.panel_width)) + 28
    local panel_w = res_x * o.panel_width - 56
    local font = "{\\fn" .. o.font .. "\\bord2\\shad0\\3c&H000000&\\1c&HFFFFFF&}"
    local events = {}

    -- Header with the keys.
    events[#events + 1] = string.format(
        "{\\an7\\pos(%d,%d)}%s{\\fs16\\1c&HAAAAAA&}↑↓ choose   R replay   M mark missed   E English %s",
        panel_x, 24, font, english_on and "(on)" or "(off)")

    -- Lines, newest at the bottom, stacked upwards.
    local y = res_y - 64
    local top_limit = 70
    for i = #shown, 1, -1 do
        local line = shown[i]
        local is_sel = (i == selected)
        local sound = is_sound_only(line.text)

        local parts = {}
        local ja_wrapped = wrap(line.text, panel_w / o.ja_size)
        for _, vl in ipairs(ja_wrapped) do parts[#parts + 1] = style_ja(vl) end
        local text = table.concat(parts, "\\N")
        local height = #ja_wrapped * o.ja_size * 1.2

        local ja_color = is_sel and "&HB29AF2&" or "&HFFFFFF&"  -- sakura pink (ASS colours are BGR)
        local block = string.format("{\\fs%d\\1c%s%s}%s", o.ja_size, ja_color,
            sound and "\\alpha&H90&" or "", text)

        if english_on then
            local en = english_for(line)
            if en ~= "" then
                local en_wrapped = wrap(en, panel_w / o.en_size)
                local en_parts = {}
                for _, vl in ipairs(en_wrapped) do en_parts[#en_parts + 1] = esc(vl) end
                block = block .. string.format("\\N{\\alpha&H00&\\fs%d\\1c&HC8C8C8&}%s",
                    o.en_size, table.concat(en_parts, "\\N"))
                height = height + #en_wrapped * o.en_size * 1.2
            end
        end

        if y - height < top_limit then break end   -- out of room: drop older lines
        local marker = is_sel and "▶ " or ""
        if marked[line.key] then marker = marker .. "★ " end
        if marker ~= "" then
            events[#events + 1] = string.format("{\\an3\\pos(%d,%d)}%s{\\fs%d\\1c&HB29AF2&}%s",
                panel_x - 6, y - height + o.ja_size * 1.2, font, math.floor(o.ja_size * 0.7), marker)
        end
        events[#events + 1] = string.format("{\\an1\\pos(%d,%d)}%s%s", panel_x, y, font, block)
        y = y - height - 26
    end

    overlay.data = table.concat(events, "\n")
    overlay:update()
end

mp.observe_property("osd-dimensions", "native", function() if panel_open then render() end end)

-- ---------------------------------------------------------------------------
-- Panel actions
-- ---------------------------------------------------------------------------

local function move(delta)
    if not selected then return end
    selected = math.max(1, math.min(#shown, selected + delta))
    render()
end

local close_panel  -- forward declaration

local function on_time(_, t)
    if replaying and replay_armed and t and t >= replay_end then
        replaying, replay_armed = false, false
        mp.unobserve_property(on_time)
        mp.set_property_bool("pause", true)   -- reopens the panel
    end
end

local function replay()
    local line = shown[selected]
    if not line then return end
    keep_key = line.key
    replaying, replay_armed = true, false
    replay_end = line.stop + o.replay_padding
    close_panel()
    mp.commandv("seek", tostring(math.max(0, line.start - o.replay_padding)), "absolute+exact")
    mp.observe_property("time-pos", "number", on_time)
    mp.set_property_bool("pause", false)
end

mp.register_event("playback-restart", function()
    -- A seek has finished: only now is time-pos meaningful for the replay.
    if replaying then replay_armed = true end
end)

local function mark()
    local line = shown[selected]
    if not line then return end
    if marked[line.key] then mp.osd_message("Already marked", 1); return end
    local path = mp.command_native({ "expand-path", o.log_path })
    local video = mp.get_property("path") or ""
    if not video:find("^%a:[\\/]") and not video:find("^[\\/]") and not video:find("://") then
        video = utils.join_path(mp.get_property("working-directory") or "", video)
    end
    local record = utils.format_json({
        video = video,
        title = mp.get_property("media-title"),
        start = line.start,
        stop = line.stop,
        ja = line.text,
        en = english_for(line),
        marked_at = os.date("!%Y-%m-%dT%H:%M:%SZ"),
    })
    local f, err = io.open(path, "a")
    if not f then
        msg.error("can't write missed-lines log " .. path .. ": " .. tostring(err))
        mp.osd_message("Couldn't save: " .. tostring(err), 3)
        return
    end
    f:write(record, "\n")
    f:close()
    marked[line.key] = true
    render()
end

local function toggle_english()
    english_on = not english_on
    render()
end

local BINDINGS = {
    { "UP", "hatsumimi-up", function() move(-1) end, { repeatable = true } },
    { "DOWN", "hatsumimi-down", function() move(1) end, { repeatable = true } },
    { "r", "hatsumimi-replay", replay },
    { "m", "hatsumimi-mark", mark },
    { "e", "hatsumimi-english", toggle_english },
}

local function open_panel()
    local now = mp.get_property_number("time-pos") or 0
    shown = recent_lines(now)
    if #shown == 0 then
        mp.osd_message("Hatsumimi: no lines heard yet", 1.5)
        return
    end
    selected = #shown
    if keep_key then
        for i, l in ipairs(shown) do if l.key == keep_key then selected = i end end
        keep_key = nil
    end
    panel_open = true
    msg.debug(string.format("panel open: %d lines, selected %d", #shown, selected))
    mp.set_property_number("video-margin-ratio-right", o.panel_width)
    for _, b in ipairs(BINDINGS) do mp.add_forced_key_binding(b[1], b[2], b[3], b[4]) end
    render()
end

close_panel = function()
    if not panel_open then return end
    panel_open = false
    for _, b in ipairs(BINDINGS) do mp.remove_key_binding(b[2]) end
    overlay:remove()
    if saved then mp.set_property_number("video-margin-ratio-right", saved.margin) end
end

mp.observe_property("pause", "bool", function(_, paused)
    if not active then return end
    if paused then
        if replaying then
            -- Either the replay just ended (on_time paused us) or the user
            -- paused by hand part-way through: either way, it's over.
            replaying, replay_armed = false, false
            mp.unobserve_property(on_time)
        end
        if not panel_open then open_panel() end
    elseif not replaying then
        close_panel()
    end
end)

-- ---------------------------------------------------------------------------
-- Per-file setup / teardown
-- ---------------------------------------------------------------------------

local function deactivate()
    close_panel()
    if replaying then
        replaying, replay_armed = false, false
        mp.unobserve_property(on_time)
    end
    if saved then
        mp.set_property_native("sub-visibility", saved.sub_vis)
        mp.set_property_native("secondary-sub-visibility", saved.sec_vis)
        mp.set_property_number("video-margin-ratio-right", saved.margin)
        saved = nil
    end
    active = false
end

local function clear(t) for k in pairs(t) do t[k] = nil end end

mp.register_event("file-loaded", function()
    deactivate()
    -- Empty in place: the recorders hold references to these exact tables,
    -- so assigning new ones ({}) would leave them filling the old ones.
    clear(ja_lines); clear(en_lines); clear(marked)
    keep_key = nil
    english_on = o.show_english

    local ja, en = find_tracks()
    if not ja then
        msg.verbose("no Japanese subtitle track: staying inactive")
        return
    end
    saved = {
        sub_vis = mp.get_property_native("sub-visibility"),
        sec_vis = mp.get_property_native("secondary-sub-visibility"),
        margin = mp.get_property_number("video-margin-ratio-right") or 0,
    }
    mp.set_property_number("sid", ja.id)
    if en then mp.set_property_number("secondary-sid", en.id) end
    mp.set_property_native("sub-visibility", false)
    mp.set_property_native("secondary-sub-visibility", false)
    active = true
    msg.info(string.format("active: Japanese track %d (%s), English track %s",
        ja.id, ja.title or ja.lang or "?", en and (en.id .. " (" .. (en.title or en.lang or "?") .. ")") or "none"))
end)

mp.register_event("end-file", deactivate)
