# silo-seasonal-lists

Shared lists of seasonal TV episodes (Christmas, Halloween and so on) for the
**Seasonal Episodes** plugin for [Silo](https://siloserver.org). Anyone can use them, and anyone can improve them.

## Use a list

In the plugin's settings, add the list's raw link to a season:

```
Christmas | 11-01 | 01-06 | https://raw.githubusercontent.com/outragedpeanut/silo-seasonal-lists/main/lists/christmas.txt
```

Silo collections cannot hold single episodes, so the plugin matches each line against the episodes in **your** library and shows only the ones you have, and only during the dates you choose.

## Lists

| File | Lines | What |
| --- | --- | --- |
| [`lists/christmas.txt`](lists/christmas.txt) | about 2,400 | Christmas episodes of live-action, animated and hybrid shows |

## Format

```
# comments start with a hash
Gilmore Girls | Santa's Secret Stuff
Frasier | S02E05
Charmed (2018) | Jingle Hell
```

- `Show | Episode title`, or `Show | S02E05` for an exact season and episode.
- Use the show's usual title. A year in brackets is fine, and a leading "The" is optional.
- Titles are compared ignoring case, punctuation and a leading "The", "A" or "An".
- One episode per line. If a show has several, repeat the show name.

## Contribute

Open a pull request that adds lines to an existing list, or adds a new file such as `lists/halloween.txt`.
Please add only episodes you are sure about, and use the title as the show's metadata (TVDB or TMDB) spells it, because that is what the plugin matches.

## Rebuilding from the wiki table

`tools/fandom_to_list.py` converts text copied from the wiki's table into list lines:

```
python3 tools/fandom_to_list.py copied.txt > christmas.txt
```

## Credits

The Christmas list comes from the Christmas Specials Wiki
(https://christmas-specials.fandom.com/wiki/List_of_Christmas_television_episodes). Credit and thanks to its contributors.
Episode titles are facts, but please check the wiki's licence before copying its text elsewhere.

The converter script and this repository's own text are provided as they are, with no warranty.
