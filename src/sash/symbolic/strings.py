from dataclasses import dataclass, field, replace
from enum import Enum
from math import inf
from typing import TYPE_CHECKING
import re

from sash.frozen import FrozenAst, freeze_thing

if TYPE_CHECKING:
    from sash.symbolic.state import State


@dataclass(frozen=True)
class SymStr:
    parts: tuple[str, ...] = field(default_factory=tuple)

    def is_simple(self) -> bool:
        """Return true if there are no adjacent strings in `parts`."""
        last_was_str = False
        for p in self.parts:
            if isinstance(p, str):
                if last_was_str:
                    return False
                last_was_str = True
            else:
                last_was_str = False
        return True

    def simplify(self) -> "SymStr":
        if self.is_simple():
            return self

        # collapse all adjacent strings into one string
        new_parts = []
        this_str = ""
        for part in self.parts:
            if isinstance(part, str):
                this_str += part
            else:
                if this_str != "":
                    new_parts.append(this_str)
                    this_str = ""
                new_parts.append(part)
        if this_str != "":
            new_parts.append(this_str)
        return SymStr(tuple(new_parts))

    def try_to_str(self) -> str | None:
        nls : list[str] = []
        for i in self.parts:
            if isinstance(i,str):
                nls.append(i)
            else:
                return None
        return "".join(nls)

    def try_without_leading_dot_slash(self, allow_returning_empty=False) -> 'SymStr':
        first_part = self.parts[0]
        if allow_returning_empty and first_part == "./" and len(self.parts) == 1:
            # The entire SymStr is exactly "./", so we can remove it entirely and return an empty SymStr
            return SymStr(())
        if first_part == "./" and len(self.parts) > 1:
            # The first part is exactly "./", so we can remove it entirely
            new_first_part = self.parts[1]
            new_parts = (new_first_part,) + self.parts[2:]
        elif first_part.startswith("./") and len(first_part) > 2:
            # The first part starts with "./", but has additional characters, so we can remove the leading "./" prefix
            new_first_part = first_part[2:]
            new_parts = (new_first_part,) + self.parts[1:]
        else:
            # The first part does not start with "./", or the entire SymStr is just "./", so we can't remove anything
            return self
        if new_first_part.startswith("/"):
            # Removing a leading "./" would expose an absolute path, which is not equivalent, so we should not modify the SymStr in this case
            return self
        return replace(self, parts=new_parts)

    def try_without_trailing_slash(self) -> 'SymStr':
        if isinstance(self.parts[-1], str):
            # only remove trailing slash if it's part of a string literal at the end
            first_parts = self.parts[:-1]
            last_part = self.parts[-1]
            if last_part.endswith("/"):
                return replace(self, parts=(first_parts + (last_part[:-1],)))
        return self


class ArbitraryType(Enum):
    APPROXIMATION = 0
    ENVIRONMENT = 1


@dataclass(frozen=True)
class CompletelyArbitrary:
    source: FrozenAst
    kind: ArbitraryType
    producing_state: "State | None" # shouldn't ever result in cyclic data, because the state that is used to compute an arbitrary value should only ever be an ancester of the state the stores it, but beware
    prefix: SymStr | None = None
    suffix: SymStr | None = None
    quoted: bool = False
    maybe_empty: bool = True

    def __eq__(self, other):
        # If the state producing this is unknown, conservatively say it can't be equal to any other
        # Another twist here, the producing state is only relevant for the APPROXIMATION kind, because
        # arbitrariness due to the environment should be the same regardless of state
        return isinstance(other, CompletelyArbitrary) \
            and self.source == other.source \
            and self.kind == other.kind \
            and (self.kind == ArbitraryType.ENVIRONMENT or self.producing_state == other.producing_state) \
            and self.producing_state is not None \
            and self.prefix == other.prefix \
            and self.suffix == other.suffix \
            and self.quoted == other.quoted \
            and self.maybe_empty == other.maybe_empty

    def __hash__(self):
        return hash((self.source, self.kind, self.producing_state if self.kind == ArbitraryType.APPROXIMATION else None, self.prefix, self.suffix, self.quoted, self.maybe_empty))

    def __repr__(self):
        return f"CompletelyArbitrary(s`{repr(self.source)[:30]}`, {self.kind}, state<{hash(self.producing_state)}>, pre:{self.prefix}, suf:{self.suffix}, quoted:{self.quoted}, maybe_empty:{self.maybe_empty})"


@dataclass(frozen=True)
class WordCount:
    min: int
    max: int | float  # use `math.inf` for infinity


@dataclass(frozen=True)
class Field:
    content: SymStr | CompletelyArbitrary
    count: WordCount

    def quote(self) -> "Field":
        content = self.content
        if isinstance(content, CompletelyArbitrary) and not content.quoted:
            content = replace(content, quoted=True)
        if isinstance(content, CompletelyArbitrary) and self.count.min > 0:
            content = replace(content, maybe_empty=False)
        # Quoting suppresses word splitting: a quoted expansion contributes exactly one shell word,
        # even when the underlying string is empty.
        return Field(content, WordCount(1, 1))

    def is_constant(self) -> bool:
        return isinstance(self.content, SymStr) and all(isinstance(p, str) for p in self.content.parts) and self.count.min == self.count.max

    def try_to_str(self) -> str | None:
        match self.content:
            case SymStr():
                return self.content.try_to_str()
            case _:
                return None

    def try_to_int(self) -> int | None:
        if (s := self.try_to_str()) and s.isdecimal():
            return int(s)
        return None

    def try_without_leading_dot_slash(self) -> "Field":
        if isinstance(self.content, SymStr):
            new_content = self.content.try_without_leading_dot_slash(False) # The entire field could be "./"
            if self.content != new_content:
                return replace(self, content=new_content)

        elif isinstance(self.content, CompletelyArbitrary) and self.content.prefix:
            new_pre = self.content.prefix.try_without_leading_dot_slash(True) # Here it's fine because we have arbitrary content
            if self.content.prefix != new_pre:
                if new_pre.try_to_str() in {"./", ""}:
                    new_pre = None
                new_content = replace(self.content, prefix=new_pre)
                return replace(self, content=new_content)

        # otherwise, do nothing
        return self

    def try_without_trailing_slash(self) -> "Field":
        if isinstance(self.content, SymStr):
            new_content = self.content.try_without_trailing_slash()
            if self.content != new_content:
                return replace(self, content=new_content)

        elif isinstance(self.content, CompletelyArbitrary) and self.content.suffix:
            new_suf = self.content.suffix.try_without_trailing_slash()
            if self.content.suffix != new_suf:
                if new_suf.try_to_str() == "":
                    new_suf = None
                new_content = replace(self.content, suffix=new_suf)
                return replace(self, content=new_content)

        # otherwise, do nothing
        return self

    @staticmethod
    def create_constant(s: str, words: int = 1) -> "Field":
        return Field(SymStr((s,)), WordCount(words, words))


def merge_counts(c1: WordCount, c2: WordCount, sep: int = 0) -> WordCount:
    """
    Calculates the resulting field count bounds when two shell words/chunks are concatenated.

    When two expansions are placed next to each other without an unquoted
    space (e.g., `${VAR1}${VAR2}`), the last field of the first expansion and the
    first field of the second expansion fuse together into a single field.

    The mathematical logic follows the formula: `Base Sum - Fusion Penalty + Separator`

    Args:
        c1 (WordCount): The minimum and maximum field bounds of the first chunk.
        c2 (WordCount): The minimum and maximum field bounds of the second chunk.
        sep (int, optional): Explicit boundary modifiers (e.g., if an explicit space
            was parsed between them). Defaults to 0.

    Returns:
        WordCount: A new WordCount representing the merged bounds.

    Logic / Edge Cases:
        - Normal Fusion: If c1=2 ("a", "b") and c2=2 ("x", "y"), the result is 3
          ("a", "bx", "y"). The logic subtracts 1 because the boundary fields fused.
        - Ghost Fields: The `-1` fusion penalty is ONLY applied if BOTH chunks
          produce at least one field (c > 0). If one chunk evaluates to an empty
          variable (0 fields), there is nothing to fuse with, so the penalty is 0.
    """
    min_merge = c1.min + c2.min - (1 if c1.min > 0 and c2.min > 0 else 0) + sep
    max_merge = c1.max + c2.max - (1 if c1.max > 0 and c2.max > 0 else 0) + sep
    return WordCount(min_merge, max_merge)


def add_prefix(arbitrary_field: Field, prefix_symstr: Field) -> Field:
    match (arbitrary_field, prefix_symstr):
        case (Field(CompletelyArbitrary(prefix=None) as a, acount),
              Field(SymStr() as s, scount)):
            return Field(replace(a, prefix=s), merge_counts(acount, scount))
        case (Field(CompletelyArbitrary(prefix=SymStr(pre_parts)) as a, acount),
              Field(SymStr(more_parts) as s, scount)):
            return Field(replace(a, prefix=SymStr(more_parts + pre_parts)), merge_counts(acount, scount))
        case _:
            assert False, "unreachable"


def add_suffix(arbitrary_field: Field, suffix_symstr: Field) -> Field:
    match (arbitrary_field, suffix_symstr):
        case (Field(CompletelyArbitrary(suffix=None) as a, acount),
              Field(SymStr() as s, scount)):
            return Field(replace(a, suffix=s), merge_counts(acount, scount))
        case (Field(CompletelyArbitrary(suffix=SymStr(suf_parts)) as a, acount),
              Field(SymStr(more_parts) as s, scount)):
            return Field(replace(a, suffix=SymStr(suf_parts + more_parts)), merge_counts(acount, scount))
        case _:
            assert False, "unreachable"


def merge_partial_fields(fields: list[Field], sep: str | None = " ", state: "State | None" = None) -> Field:
    def merge_symstrs(symstrs: list[Field]) -> Field:
        assert all(isinstance(f.content, SymStr) for f in symstrs), f"merge_symstrs should only be called on lists of Fields with SymStr content (got {symstrs})"
        match symstrs:
            case []:
                return Field(SymStr(()), WordCount(0, 0))
            case [one]:
                return one
            case [Field(SymStr(parts), c), *rest]:
                content = parts
                count = c
                for field in rest:
                    content = content + ((sep,) if sep else ()) + field.content.parts # type: ignore (field.content is SymStr due to assert above)
                    count = merge_counts(count, field.count, 1 if sep else 0)
                return Field(SymStr(tuple(content)), count)
            case _:
                assert False, "unreachable"

    def collect_prefixes_suffixes(fields: list[Field]) -> tuple[Field | None, Field | None]:
        prefixes = []
        for field in fields:
            if isinstance(field.content, SymStr):
                prefixes.append(field)
            else:
                break
        suffixes = []
        for field in reversed(fields):
            if isinstance(field.content, SymStr):
                suffixes.append(field)
            else:
                break
        return (merge_symstrs(prefixes) if prefixes else None,
                merge_symstrs(suffixes) if suffixes else None)

    num_arbitraries = sum(1 for field in fields if isinstance(field.content, CompletelyArbitrary))
    if num_arbitraries == 0:
        # just join the symstrs
        return merge_symstrs(fields)
    elif num_arbitraries == 1:
        arbitrary = [field for field in fields if isinstance(field.content, CompletelyArbitrary)][0]
        prefix, suffix = collect_prefixes_suffixes(fields)
        if prefix is not None:
            arbitrary = add_prefix(arbitrary, prefix)
        if suffix is not None:
            arbitrary = add_suffix(arbitrary, suffix)
        return arbitrary
    else:
        # multiple arbitraries -- give up and return a new arbitrary field
        arbitraries = [field for field in fields if isinstance(field.content, CompletelyArbitrary)]
        prefix, suffix = collect_prefixes_suffixes(fields)
        quoted = all(a.content.quoted for a in arbitraries) # type: ignore
        if state is not None:
            arbitrary = Field(CompletelyArbitrary(freeze_thing([a.content.source for a in arbitraries]), # type: ignore
                                                  ArbitraryType.APPROXIMATION,
                                                  state,
                                                  quoted=quoted),
                                                  WordCount(0, inf))
        else:
            base = arbitraries[0].content
            maybe_empty = any(a.content.maybe_empty for a in arbitraries) # type: ignore
            arbitrary = Field(CompletelyArbitrary(base.source, # type: ignore
                                                  ArbitraryType.APPROXIMATION,
                                                  base.producing_state, # type: ignore
                                                  quoted=quoted,
                                                  maybe_empty=maybe_empty),
                                                  WordCount(0, inf))
        if prefix is not None:
            arbitrary = add_prefix(arbitrary, prefix)
        if suffix is not None:
            arbitrary = add_suffix(arbitrary, suffix)
        return arbitrary


@dataclass(frozen=True)
class LiteralChunk:
    """
    Text explicitly typed by the programmer in the source code.
    Origin: Generated by the AST parser before execution begins.

    Splitting Rules:
    - NEVER subject to IFS field splitting (only expansions split).

    Globbing Rules:
    - Subject to pathname expansion ONLY IF `is_quoted == False`.
    """
    content: str
    is_quoted: bool


@dataclass(frozen=True)
class ExpandedChunk:
    """
    Text resulting from parameter/command/arithmetic expansion.
    Origin: Evaluation state.

    Splitting rules:
    - Subject to field splitting ONLY IF `is_quoted == False`.

    Globbing rules:
    - Subject to pathname expansion ONLY IF `is_quoted == False`.
    """
    content: str | CompletelyArbitrary
    is_quoted: bool
    count: WordCount


@dataclass(frozen=True)
class PreSplitWord:
    """
    The core Intermediate Representation for a shell word.
    Preserves the boundary between literal text and symbolic expansions
    until the context demands evaluation (splitting, globbing, or storage).
    """
    chunks: list[LiteralChunk | ExpandedChunk]

    def prepare_for_storage(self) -> 'PreSplitWord':
        """
        CONTEXT: Assignment Context (e.g., VAR="a b" or VAR=$OTHER)

        Removes the AST quoting context, as quotes are consumed by the assignment.
        Returns a new PreSplitWord safe to be stored in the symbolic state variable map.
        """
        stored_chunks = []
        for chunk in self.chunks:
            if isinstance(chunk, LiteralChunk):
                stored_chunks.append(LiteralChunk(content=chunk.content, is_quoted=False))
            elif isinstance(chunk, ExpandedChunk):
                stored_chunks.append(
                    ExpandedChunk(
                        content=chunk.content,
                        is_quoted=False,
                        count=chunk.count
                    )
                )
        return PreSplitWord(stored_chunks)

    def expand_from_storage(self, in_quoted_context: bool) -> 'PreSplitWord':
        """
        CONTEXT: Variable Reference (e.g., $VAR or "$VAR")

        Converts stored chunks into ExpandedChunks (because retrieving a var
        IS an expansion), applying the current AST quoting context.
        """
        retrieved_chunks = []
        for chunk in self.chunks:
            # If the stored chunk is completely arbitrary, preserve its count.
            # If it was a literal string, it is guaranteed to have a count of 1.
            chunk_count = getattr(chunk, 'count', WordCount(1, 1))

            retrieved_chunks.append(
                ExpandedChunk(
                    content=chunk.content,
                    is_quoted=in_quoted_context,
                    count=chunk_count
                )
            )
        return PreSplitWord(retrieved_chunks)

    def do_field_splitting(self, ifs_value: str) -> list[Field]:
        """
        CONTEXT: Command Execution (e.g., echo $VAR, ls *.txt).

        Executes Field Splitting. Iterates through chunks and splits
        unquoted ExpandedChunks based on the provided IFS string.
        """
        if not self.chunks:
            return []

        resulting_fields: list[Field] = []
        current_parts: list[Field] = []
        current_text: list[tuple[str, bool]] = []

        def flush_text() -> None:
            if not current_text:
                return
            joined = "".join(text for text, _ in current_text)
            has_glob = any(eligible and "*" in text for text, eligible in current_text)
            min_words = 1
            max_words: int | float = 1
            if has_glob:
                max_words = float("inf")
                only_globs = True
                for text, eligible in current_text:
                    if not text:
                        continue
                    if not eligible:
                        only_globs = False
                        break
                    if any(ch != "*" for ch in text):
                        only_globs = False
                        break
                if only_globs:
                    min_words = 0
            current_parts.append(Field(SymStr((joined,)), WordCount(min_words, max_words)))
            current_text.clear()

        def commit_field(allow_empty: bool = False) -> None:
            flush_text()
            if not current_parts:
                if allow_empty:
                    resulting_fields.append(Field(SymStr(("",)), WordCount(1, 1)))
                return
            resulting_fields.append(merge_partial_fields(current_parts))
            current_parts.clear()

        def split_by_ifs(text: str) -> tuple[list[str], bool]:
            if text == "":
                return [], False
            if ifs_value == "":
                return [text], False
            ifs_chars = ifs_value if ifs_value is not None else " \t\n"
            whitespace = {ch for ch in ifs_chars if ch in " \t\n"}
            non_whitespace = [ch for ch in ifs_chars if ch not in " \t\n"]

            if not non_whitespace:
                parts = [p for p in re.split(r"[ \t\n]+", text) if p != ""]
                return parts, False
            if not whitespace:
                pattern = f"[{re.escape(''.join(non_whitespace))}]"
                return re.split(pattern, text), True

            pattern = f"[{re.escape(''.join(non_whitespace))}]"
            pieces = re.split(pattern, text)
            result: list[str] = []
            for piece in pieces:
                if piece == "":
                    result.append("")
                    continue
                result.extend([p for p in re.split(r"[ \t\n]+", piece) if p != ""])
            return result, True

        for chunk in self.chunks:
            if isinstance(chunk, LiteralChunk):
                current_text.append((chunk.content, not chunk.is_quoted))
                continue

            if chunk.is_quoted:
                if isinstance(chunk.content, CompletelyArbitrary):
                    flush_text()
                    current_parts.append(Field(chunk.content, chunk.count).quote())
                else:
                    current_text.append((chunk.content, False))
                continue

            if isinstance(chunk.content, CompletelyArbitrary):
                flush_text()
                current_parts.append(Field(chunk.content, chunk.count))
                continue

            segments, preserve_empty = split_by_ifs(chunk.content)
            if not preserve_empty:
                for i, segment in enumerate(segments):
                    if not segment:
                        continue
                    current_text.append((segment, True))
                    if i < len(segments) - 1:
                        commit_field()
                continue

            for i, segment in enumerate(segments):
                if segment:
                    current_text.append((segment, True))
                if i < len(segments) - 1:
                    if segment:
                        commit_field()
                    else:
                        if current_parts or current_text:
                            commit_field()
                        commit_field(allow_empty=True)

            if segments and segments[-1] == "":
                if current_parts or current_text:
                    commit_field()
                commit_field(allow_empty=True)

        commit_field()
        return resulting_fields

    def to_field(self) -> Field:
        if not self.chunks:
            return Field(SymStr(()), WordCount(0, 0))
        fields: list[Field] = []
        for chunk in self.chunks:
            if isinstance(chunk, LiteralChunk):
                fields.append(Field(SymStr((chunk.content,)), WordCount(1, 1)))
            elif isinstance(chunk.content, str):
                fields.append(Field(SymStr((chunk.content,)), chunk.count))
            else:
                fields.append(Field(chunk.content, chunk.count))
        return merge_partial_fields(fields)

    def try_to_str(self) -> str | None:
        return self.to_field().try_to_str()

    @staticmethod
    def from_field(field: Field) -> "PreSplitWord":
        content = field.content
        if isinstance(content, SymStr):
            text = content.try_to_str()
            if text is not None:
                return PreSplitWord([
                    ExpandedChunk(content=text, is_quoted=False, count=field.count),
                ])
            arbitrary = CompletelyArbitrary(freeze_thing(content), ArbitraryType.APPROXIMATION, None)  # type: ignore[arg-type]
            return PreSplitWord([
                ExpandedChunk(content=arbitrary, is_quoted=False, count=field.count),
            ])
        return PreSplitWord([
            ExpandedChunk(content=content, is_quoted=False, count=field.count),
        ])
