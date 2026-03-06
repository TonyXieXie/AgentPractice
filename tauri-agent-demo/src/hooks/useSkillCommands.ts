import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { SkillSummary } from '../types';
import {
  buildSkillCommandPattern,
  buildSkillInvocationPattern,
  findCommandTrigger,
  findSkillInvocation,
  stripExistingSkillCommands,
} from '../app/shared';
import type { CommandItem } from '../app/shared';

type UseSkillCommandsParams = {
  autoScrollRef: { current: boolean };
  inputMsg: string;
  inputRef: { current: HTMLTextAreaElement | null };
  setActiveSkill: (skill: SkillSummary | null) => void;
  setInputMsg: (value: string) => void;
  skills: SkillSummary[];
};

export function useSkillCommands({
  autoScrollRef,
  inputMsg,
  inputRef,
  setActiveSkill,
  setInputMsg,
  skills,
}: UseSkillCommandsParams) {
  const [commandSuggestions, setCommandSuggestions] = useState<CommandItem[]>([]);
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [commandActiveIndex, setCommandActiveIndex] = useState(0);
  const commandTriggerRef = useRef<{ start: number; end: number; query: string } | null>(null);
  const commandQueryRef = useRef('');

  const commandItems = useMemo<CommandItem[]>(
    () =>
      skills.map((skill) => ({
        kind: 'skill',
        id: skill.name,
        label: skill.name,
        description: skill.description,
        insertText: `$${skill.name}`,
      })),
    [skills]
  );

  const skillCommandPattern = useMemo(() => buildSkillCommandPattern(skills), [skills]);
  const skillInvocationPattern = useMemo(() => buildSkillInvocationPattern(skills), [skills]);
  const skillLookup = useMemo(() => {
    const map = new Map<string, SkillSummary>();
    skills.forEach((skill) => {
      if (skill?.name) {
        map.set(skill.name.toLowerCase(), skill);
      }
    });
    return map;
  }, [skills]);

  const closeCommandSuggestions = useCallback(() => {
    setShowCommandSuggestions(false);
    setCommandSuggestions([]);
    setCommandActiveIndex(0);
    commandTriggerRef.current = null;
    commandQueryRef.current = '';
  }, []);

  const updateCommandSuggestions = useCallback((value: string, cursor: number | null) => {
    const trigger = findCommandTrigger(value, cursor);
    if (!trigger) {
      closeCommandSuggestions();
      return;
    }
    const query = trigger.query.toLowerCase();
    commandTriggerRef.current = trigger;

    if (!commandItems.length) {
      commandQueryRef.current = query;
      setCommandSuggestions([]);
      setShowCommandSuggestions(true);
      setCommandActiveIndex(0);
      return;
    }

    const matches = commandItems.filter((item) => {
      if (!query) return true;
      const label = item.label.toLowerCase();
      const desc = item.description?.toLowerCase() || '';
      return label.includes(query) || desc.includes(query);
    });
    const queryChanged = commandQueryRef.current !== query;
    commandQueryRef.current = query;
    setCommandSuggestions(matches);
    setShowCommandSuggestions(true);
    setCommandActiveIndex((prev) => {
      if (queryChanged) return 0;
      return prev >= matches.length ? 0 : prev;
    });
  }, [commandItems, closeCommandSuggestions]);

  const applyInputValue = useCallback(
    (rawValue: string, cursor: number | null, options?: { normalizeSkill?: boolean }) => {
      let nextValue = rawValue;
      let nextCursor = cursor;
      let matchedSkill: SkillSummary | null = null;
      let changed = false;
      const shouldNormalize = options?.normalizeSkill !== false;

      if (shouldNormalize && skillInvocationPattern && skillCommandPattern) {
        const match = findSkillInvocation(rawValue, skillInvocationPattern);
        if (match) {
          matchedSkill = skillLookup.get(match.name.toLowerCase()) ?? null;
          nextValue = stripExistingSkillCommands(rawValue, skillCommandPattern).replace(/^\s+/, '');
          if (cursor != null) {
            const before = rawValue.slice(0, cursor);
            const nextBefore = stripExistingSkillCommands(before, skillCommandPattern);
            nextCursor = nextBefore.length;
          }
          changed = nextValue !== rawValue;
        }
      }

      if (matchedSkill) {
        setActiveSkill(matchedSkill);
      }
      setInputMsg(nextValue);
      autoScrollRef.current = true;
      updateCommandSuggestions(nextValue, nextCursor);

      if (changed) {
        requestAnimationFrame(() => {
          const input = inputRef.current;
          if (!input) return;
          const caret = nextCursor ?? nextValue.length;
          input.setSelectionRange(caret, caret);
        });
      }
    },
    [autoScrollRef, inputRef, setActiveSkill, setInputMsg, skillCommandPattern, skillInvocationPattern, skillLookup, updateCommandSuggestions]
  );

  useEffect(() => {
    if (!inputMsg || !skillInvocationPattern) return;
    const match = findSkillInvocation(inputMsg, skillInvocationPattern);
    if (!match) return;
    applyInputValue(inputMsg, inputRef.current?.selectionStart ?? null);
  }, [applyInputValue, inputMsg, inputRef, skillInvocationPattern]);

  const applyCommandSuggestion = useCallback((command: CommandItem) => {
    const trigger = commandTriggerRef.current;
    const currentValue = inputRef.current?.value ?? inputMsg;
    if (!trigger) return;
    const beforeRaw = currentValue.slice(0, trigger.start);
    const afterRaw = currentValue.slice(trigger.end);

    if (command.kind === 'skill') {
      const withoutTrigger = `${beforeRaw}${afterRaw}`;
      const nextValue = stripExistingSkillCommands(withoutTrigger, skillCommandPattern).replace(/^\s+/, '');
      const skill = skillLookup.get(command.id.toLowerCase()) ?? null;
      if (skill) {
        setActiveSkill(skill);
      }
      setInputMsg(nextValue);
      closeCommandSuggestions();
      requestAnimationFrame(() => {
        const input = inputRef.current;
        if (!input) return;
        const before = withoutTrigger.slice(0, trigger.start);
        const caret = stripExistingSkillCommands(before, skillCommandPattern).length;
        input.focus();
        input.setSelectionRange(caret, caret);
      });
      return;
    }

    const before = stripExistingSkillCommands(beforeRaw, skillCommandPattern);
    const after = stripExistingSkillCommands(afterRaw, skillCommandPattern);
    const insertion = `${command.insertText} `;
    const nextValue = `${before}${insertion}${after}`;
    setInputMsg(nextValue);
    closeCommandSuggestions();
    requestAnimationFrame(() => {
      const input = inputRef.current;
      if (!input) return;
      const caret = before.length + insertion.length;
      input.focus();
      input.setSelectionRange(caret, caret);
    });
  }, [closeCommandSuggestions, inputMsg, inputRef, setActiveSkill, setInputMsg, skillCommandPattern, skillLookup]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    if (document.activeElement !== input) return;
    const value = input.value ?? '';
    if (!value) {
      closeCommandSuggestions();
      return;
    }
    const cursor = Number.isFinite(input.selectionStart) ? input.selectionStart : value.length;
    updateCommandSuggestions(value, cursor);
  }, [commandItems, closeCommandSuggestions, inputRef, updateCommandSuggestions]);

  return {
    applyCommandSuggestion,
    applyInputValue,
    closeCommandSuggestions,
    commandActiveIndex,
    commandItems,
    commandQuery: commandQueryRef.current,
    commandSuggestions,
    setCommandActiveIndex,
    showCommandSuggestions,
    skillInvocationPattern,
    updateCommandSuggestions,
  };
}
