"""The flang brain, printed into Python by the compiler.

Nothing here is written by hand and nothing here is edited: the two modules
beside this one are the output of ``flang emit`` over
``ouroboros/brain/trace_brain.flang``, and ``scripts/emit_brain.py --check``
fails if they stop matching a fresh print. Change the rule in the flang source,
print again, commit the result.

``printed-from.txt`` beside them says which source, and which compiler, this
print was made from. It is what catches an edit the print never sees — a
``note``, or a postcondition the proof kernel closes and strips — and it is
checked without a compiler, so it is checked everywhere.

They are committed rather than printed at build time so that installing the
tool needs pip and nothing else — see ``docs/sdd/brain-in-flang.md`` for what
that costs and what the alternative would have cost.
"""
