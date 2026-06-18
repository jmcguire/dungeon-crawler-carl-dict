# Notes from Google's AI

Building custom Kindle dictionaries for long fantasy novels is an excellent way to elevate the world-building, but you must be strategic about how you structure your inflection blocks.
The short answer is no, idx:infl tags do not inherently slow down Kindle lookups, provided you follow the proper technical guidelines. Kindle uses a pre-compiled, highly optimized binary index (MOBI 7 format) where lookups are nearly instantaneous. Performance bottlenecks happen only if your source files are poorly formatted or structurally fragmented. [1, 2, 3] 
The specific technical practices, structural rules, and indexing realities for building a fantasy glossary provide a clear blueprint for success.

------------------------------

## 1. Inflections vs. Full Aliases: The Best Practice
For an epic fantasy dictionary, you should absolutely use <idx:infl> for both basic grammar (plurals) and narrative aliases (titles, nicknames, or common misspellings). [4, 5, 6] 

* The Rule of Thumb: If a reader highlights a piece of text in the novel, and you want them to see this specific entry's definition, that text must be declared in an <idx:iform> tag within that entry’s <idx:infl> block. [4, 5] 
* Grammar Example: If your fictional race is a Kaladin, you need inflections for Kaladins (plural) and Kaladin's (possessive). [5, 6] 
* Alias Example: If a character is named Morgath, but characters also call him The Pale King, The Shadow Weaver, or Morg, those should be added as inflections so highlighting any of those titles brings up Morgath's entry. [4] 

## Correct Technical Syntax:

<idx:entry name="glossary" scriptable="yes" spell="yes">
  <idx:orth value="Morgath">
    <idx:infl>
      <!-- Grammatical Inflections -->
      <idx:iform value="Morgath's" />
      <!-- Worldbuilding Aliases -->
      <idx:iform value="The Pale King" />
      <idx:iform value="The Shadow Weaver" />
      <idx:iform value="Morg" />
    </idx:infl>
  </idx:orth>
  <h2>Morgath</h2>
  <p>The immortal sorcerer-king who ruled the Northern Wastes during the Second Age...</p>
</idx:entry>

------------------------------

## 2. Do Inflections Slow Down Kindle Lookups?
No. The rumor that inflections slow down the Kindle stems from a misunderstanding of how the Kindle software handles files. [1] 
When you build a dictionary, tools like [Kindle Previewer](https://kdp.amazon.com/en_US/help/topic/G2HXJS944GL88DNV) compile your XHTML into a flat database. When a user highlights a word, the Kindle does not scan through your book linearly; it performs a rapid binary lookup against a pre-compiled index file (.mobi or .prc internal indexes). Adding 10 or 20 inflections to an entry adds a negligible amount of metadata to that index. [1, 7, 8] 
## What actually slows down lookups:

* Massive File Sizes: If your uncompiled source XHTML file is tens of megabytes long in a single file, the compiler might struggle, causing index corruption. Split your source text into smaller, organized XHTML chapters before compiling. [1] 
* Unindexed Books in the Background: If the Kindle feels sluggish overall during lookups, it is usually because a newly downloaded novel is still background-indexing text. [3] 
* Invalid HTML/OPF Structure: Missing <mbp:frameset> tags or mismatched <DefaultLookupIndex> entries in your OPF metadata will cause the Kindle to error out or search endlessly. [2, 9] 

------------------------------

## 3. Are they indexed appropriately?
Yes, but with crucial Kindle constraints you must account for during your fantasy world-building:

* The Multi-Word Constraint: Historically, Kindle's lookup index handled multi-word inflections well. However, [recent Amazon UI updates](https://www.reddit.com/r/kindle/comments/1qa67q0/what_the_hell_did_they_do_to_the_dictionary/) have made the hardware lookup engine more restrictive, sometimes limiting on-page taps strictly to single words. If a user highlights just the word "Pale" in "The Pale King", it won't trigger Morgath's entry unless you've accounted for individual tokens. [10] 
* The Solution for Character Names: If you have characters with shared names (e.g., John Smith vs. Adam Smith), you must structure the inflections to include individual pieces of the name so the index can properly map either word to the correct entry block. [4] 
* Exact Matching Only: Kindle does not use modern "fuzzy" search or regex for dictionary pop-ups. If your character's name is Elend, and the text reads Elend-like, the Kindle will fail to find it unless you explicitly added <idx:iform value="Elend-like" />. [6] 

## Final Checklist for Compilation
To ensure your inflections index beautifully without rendering errors:

   1. Ensure your .opf file has the <DictionaryInLanguage> and <DictionaryOutLanguage> metadata tags set correctly to match the book your reader is viewing.
   2. Wrap your entire layout in <mbp:frameset>.
   3. Compile using an older copy of KindleGen or pass an EPUB through Kindle Previewer to test the index dynamically. [1, 2, 7, 8, 9] 

If you would like, I can provide a complete starter template for a .opf metadata file or help you write a Python script to automatically generate inflections for your character list. Let me know how you'd like to proceed!

[1] [https://stackoverflow.com](https://stackoverflow.com/questions/70017931/creating-a-kindle-dictionary)
[2] [https://www.scribd.com](https://www.scribd.com/document/409392496/015)
[3] [https://www.amazonforum.com](https://www.amazonforum.com/s/question/0D5Kf00003WwxF3KAJ/kindle-paperwhite-12th-gen-dictionary-noticeably-slower-since-5181-update?language=en_US)
[4] [https://www.reddit.com](https://www.reddit.com/r/kindle/comments/10zt3y5/custom_dictionary_for_kindle/)
[5] [https://g-ecx.images-amazon.com](http://g-ecx.images-amazon.com/images/G/01/digital/kindle/merch/AmazonKindlePublishingGuidelines_update.pdf)
[6] [https://law-hawaii.libguides.com](https://law-hawaii.libguides.com/search_strategies)
[7] [https://kdp.amazon.com](https://kdp.amazon.com/en_US/help/topic/G2HXJS944GL88DNV)
[8] [https://www.mobileread.com](https://www.mobileread.com/forums/showthread.php?t=343123)
[9] [https://jakemccrary.com](https://jakemccrary.com/blog/2020/11/11/creating-a-custom-kindle-dictionary/)
[10] [https://www.reddit.com](https://www.reddit.com/r/kindle/comments/1qa67q0/what_the_hell_did_they_do_to_the_dictionary/)


# Notes from ChatGPT


Use idx:infl / idx:iform for **any alternate lookup form that should resolve
to the same entry**, not only grammatical inflections.                      
                                                                            
Technically, Amazon describes idx:infl as building a hidden inflection      
index, but also says deprecated idx:key used to support “alternative lookup 
wordform” and that those alternative lookup forms should now be wrapped with
idx:infl / idx:iform. So, yes: aliases are a legitimate use, not just       
plurals/conjugations. The important caveat is that idx:iform entries are    
**hidden**: they support in-book lookup, but they do not appear as normal   
searchable/browsable headwords in the dictionary index. Amazon says the     
inflection index is “not directly searchable by the user” and is used for in-
book lookup. citeturn502297view0                                         
                                                                            
Best practice for your fictional dictionary:                                
                                                                            
Use idx:orth for the canonical, user-visible entry.                         
                                                                            
  <idx:orth value="Valtay Corporation"><b>Valtay Corporation</b>            
                                                                            
Use idx:iform for exact surface variants that should silently route to that 
entry:                                                                      
                                                                            
  <idx:infl>                                                                
    <idx:iform value="Valtay" />                                            
    <idx:iform value="The Valtay Corporation" />                            
    <idx:iform value="Valtay Corporation's" />                              
    <idx:iform value="Valtay Corp" />                                       
  </idx:infl>                                                               
                                                                            
Use a **separate **idx:orth** entry** when the alias is something a user    
might expect to find while browsing or typing in the dictionary itself. For 
example, if “Valtay” should appear in the dictionary index, make it its own 
headword, either duplicating the definition or saying “See Valtay           
Corporation.” Do not rely on idx:iform for browsable aliases.               
                                                                            
So the practical rule is:                                                   
                                                                            
idx:orth = visible dictionary headword.                                     
idx:iform = hidden lookup trigger.                                          
                                                                            
On slowdown: yes, there is a real basis for that concern, but it is not     
“every idx:iform is bad.” Amazon says optional inflection categories can    
expand the inflection index and “may result in slower performance during    
word lookup.” That implies these forms are indexed, not scanned as raw HTML,
but a huge hidden lookup index can still affect performance.                
citeturn502297view0                                                      
                                                                            
For your use case, I would keep idx:iform forms tight and mechanical:       
                                                                            
Canonical: Valtay Corporation                                               
Good idx:iforms: Valtay, The Valtay Corporation, Valtay Corporation's,      
Valtay Corp.                                                                
Bad idx:iforms: the sponsor, corporation, evil company, that company Carl   
gets sponsored by                                                           
                                                                            
Those broader aliases should either be omitted or made separate visible     
cross-reference entries.                                                    
                                                                            
Also avoid the optional inflgrp and name attributes unless you actually need
grammatical categories. For fiction lookup, they add size without much      
benefit. Amazon’s example uses them for noun/verb paradigms, but your terms 
are entities, not morphology.                                               

