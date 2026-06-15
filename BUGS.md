# Stuff to do next

## Bugs:

 - Categories and Subcategories can technically contain a loop. Make sure we don't get caught in a loop. (DCC doesn't have any loops, but it's a good piece of defensive coding.)
 - one entry, "Torch (Item)", get rid of the (Item).
 - fetch_characters should be renamed fetch_entries
 - can we fix these:
      - Krakaren
      - Krakaren Clone (Fourth Floor)
      - Krakaren Clone (Second Floor)

## Todo:

 - Make the default categories be all the categories we normally use
 - add inflections for for some things, like

    <idx:orth>Carl</idx:orth>
    <idx:infl>
      <idx:iform value="carl's" />
    </idx:infl>

  - change the identifier in the XML files to a dictionary name + version
  - make sure the list of "entries" makes sense, i'm worried the script is stripping the last word incorrectly at times.
      - `grep idx:orth build/dictionary.xhtml | perl -nE'/value="([^"]+)"/; say $1'`
      - single quotes are all &#x27;, is that good for kindle lookup?
  - should we also strip " Achievement" off the end? also " Potion"? and the prefix "Potion of "
  - quality of life update: if there are multiple entries for one word, we should be able to show multiple entries. (Earth, Earth Box)
  - for people with clear firstname lastname (only humans, i think), also let the search by just firstname or just lastname?

## Missing:

 - Shambling Berserker
 - The Final War (spell)

## Missing  aliases

 - Grimaldi exists, but not Ringmaster Grimaldi, https://dungeon-crawler-carl.fandom.com/wiki/Grimaldi, the page title is Ringmaster Grimaldi, but the URL is Grimaldi. There's a redirect in there. We need to capture both. The whole thing is weird.
 - Valtay (Valtay Corporation exists)
 - Borant (this Borant Corporation exists, with this: "A Syndicate company, the Borant Corporation (aka Borant)")
 - Gravy Boat (is actually Ferdinand)
 - Prince Stalwart (Stalwart exists)
 - Daniel Bautista (Bautista exists)
 - https://dungeon-crawler-carl.fandom.com/wiki/Saccathian Saccathian has an alias, Sac. We should capture that. We should look for other aliases where it's like, "Saccathian (or Sacs)".
 - Null (Nullian exists)
 - Brain Boilers (Brain Boiler exists, need inflection)

## Exists but wasn't found

 - Lucia Mar
 - Miss Quill
 - Skull Empire
 - King Rust
 - Katia Grim (and the alias Katia should exist but doesn't, this is a human name thing)
 - Suppurating Eye (Suppurating Eye Spell does exist, and i think it has the alias. is it being indexed correctly?)
 - Sheol Glass Reaper Case

## Dictionary doesn't even appear:

(Why wouldn't it appear?)

 - "Heal spell" (with lowecase s) doesn't work. need inflections to get to the lowercase variants? or actually the dictionary isn't even showing up as n option to click on, maybe it;s because Heal is in italics?
 - "street urchin" dict doesn't appear
 - Kua-Tin, Kua-Tin Company
 - Lucia Mar
