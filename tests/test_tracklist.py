from cuttycat.tracklist import (ORDER_ARTIST_TITLE, ORDER_TITLE, ORDER_TITLE_ARTIST, TIMES_LENGTH,
                                TIMES_START, description_tracklist, fmt, guess_from_filename,
                                parse_time, parse_tracklist, yt_entries)


def titles(es):
    return [e["title"] for e in es]


def test_time_helpers():
    assert parse_time("3:41") == 221
    assert parse_time("1:02:10.5") == 3730.5
    assert fmt(221, 0) == "03:41"
    assert fmt(3730, 0) == "1:02:10"


def test_numbered_artist_title_with_lengths():
    es, info = parse_tracklist("1. Blame - Everything But the Girl (3:41)\n"
                               "2. Masahiro Andoh - Kiss You Good-bye (3:24)\n"
                               "3. Pauline Wilson - Like The Wind 5:20")
    assert info["order"] == ORDER_ARTIST_TITLE and info["times"] == TIMES_LENGTH
    assert [e["artist"] for e in es] == ["Blame", "Masahiro Andoh", "Pauline Wilson"]
    assert [e["start"] for e in es] == [0, 221, 425]


def test_youtube_timestamps_title_artist():
    es, info = parse_tracklist("0:00 Final Lap - Isamu Ohira\n2:37 The Drift of Air - Isamu Ohira\n"
                               "7:28 Moon Over - Isamu Ohira")
    assert info["order"] == ORDER_TITLE_ARTIST and info["times"] == TIMES_START
    assert titles(es) == ["Final Lap", "The Drift of Air", "Moon Over"]
    assert [e["start"] for e in es] == [0, 157, 448]


def test_bandcamp_split_lines_trailing_start_times():
    text = ("1.\nAt a Confluence 00:00\n2.\nRainbow Desert   06:57\n3.\nAbove Myself   11:46\n"
            "4.\nSpider Bite   15:46")
    es, info = parse_tracklist(text)
    assert info["order"] == ORDER_TITLE and info["times"] == TIMES_START
    assert titles(es) == ["At a Confluence", "Rainbow Desert", "Above Myself", "Spider Bite"]
    assert es[1]["start"] == 417


def test_vinyl_sides_and_tabs():
    es, _ = parse_tracklist("A1\tFloating Away\t3:41\nA2\tSunset Drive\t4:05\nB1\tNight Call\t5:12")
    assert [e["num"] for e in es] == ["A1", "A2", "B1"]
    assert titles(es) == ["Floating Away", "Sunset Drive", "Night Call"]


def test_title_starting_with_a_number_is_kept():
    es, _ = parse_tracklist("Gehen die Leute\nSie wollen uns erzählen\n99 Luftballons")
    assert titles(es)[2] == "99 Luftballons"


def test_time_ranges_and_junk():
    es, _ = parse_tracklist("1) 00:00 - 03:12 First [Official Video]\n2) 03:12 - 07:56 Second (HD)")
    assert titles(es) == ["First", "Second"]
    assert es[0]["length"] == 192


def test_quotes_are_stripped():
    es, _ = parse_tracklist("1. Unknown - “Menu2”\n2. Unknown - \"ID Deep\"\n3. A - B")
    assert titles(es)[:2] == ["Menu2", "ID Deep"]


def test_description_without_times():
    desc = ("https://example.com\n\nSome words about the mix.\n\n"
            "(Lead-in) Nuspirit Helsinki - Honest (Jazzanova Remix)\n\n"
            "(Intro) Track 1: Sci-Clone - Close\n\nTrack 2: Santiago Santamaria - Night Lights\n\n"
            "Track 3: Hajime Yoshizawa - Secret Flight\n\nTrack 4: Robin Jones - Royal Conga\n")
    assert len(description_tracklist(desc)) == 5
    es, src = yt_entries({"description": desc})
    assert src == "description-untimed"
    assert es[1]["artist"] == "Sci-Clone" and es[1]["title"] == "Close"
    assert all(e["start"] is None for e in es)


def test_chapters_win():
    info = {"chapters": [{"start_time": 0, "title": "Intro"}, {"start_time": 5, "title": "Main"}],
            "description": "0:00 x\n1:00 y\n2:00 z"}
    es, src = yt_entries(info)
    assert src == "chapters" and titles(es) == ["Intro", "Main"]


def test_guess_from_filename():
    assert guess_from_filename("Hoodoo Lord - Timelapse Freefall (Full Album 2026)") == \
        ("Hoodoo Lord", "Timelapse Freefall", "2026")
