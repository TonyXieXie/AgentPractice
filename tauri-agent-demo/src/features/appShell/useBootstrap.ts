import { useCallback, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import { getAppConfig, getConfigs, getDefaultConfig, getSkills } from '../../shared/api';
import type { AgentConfig, AgentTargetSelection, LLMConfig, SkillSummary } from '../../types';

type UseAppShellBootstrapParams = {
  setCurrentConfig: Dispatch<SetStateAction<LLMConfig | null>>;
  setShowConfigManager: Dispatch<SetStateAction<boolean>>;
  setAllConfigs: Dispatch<SetStateAction<LLMConfig[]>>;
  setAgentConfig: Dispatch<SetStateAction<AgentConfig | null>>;
  setSelectedAgentTarget: Dispatch<SetStateAction<AgentTargetSelection | null>>;
  setCurrentActiveAgentProfileId: Dispatch<SetStateAction<string | null>>;
  setSkills: Dispatch<SetStateAction<SkillSummary[]>>;
};

export function useAppShellBootstrap({
  setCurrentConfig,
  setShowConfigManager,
  setAllConfigs,
  setAgentConfig,
  setSelectedAgentTarget,
  setCurrentActiveAgentProfileId,
  setSkills,
}: UseAppShellBootstrapParams) {
  const resolveAgentProfileId = useCallback((config: AgentConfig | null, desired?: string | null) => {
    if (!config) return desired ?? null;
    const profiles = config.profiles || [];
    if (!profiles.length) return desired ?? null;
    if (desired && profiles.some((profile) => profile.id === desired)) return desired;
    if (config.default_profile && profiles.some((profile) => profile.id === config.default_profile)) {
      return config.default_profile;
    }
    return profiles[0].id;
  }, []);

  const resolveAgentTeam = useCallback((config: AgentConfig | null, desired?: string | null) => {
    if (!config) return null;
    const teams = config.teams || [];
    if (!desired) return null;
    return teams.find((team) => team.id === desired) || null;
  }, []);

  const resolveAgentTargetSelection = useCallback(
    (config: AgentConfig | null, desired?: AgentTargetSelection | null): AgentTargetSelection | null => {
      if (desired?.kind === 'team') {
        const team = resolveAgentTeam(config, desired.id);
        if (team) {
          return { kind: 'team', id: team.id };
        }
      }
      const desiredProfileId = desired?.kind === 'profile' ? desired.id : null;
      const profileId = resolveAgentProfileId(config, desiredProfileId);
      return profileId ? { kind: 'profile', id: profileId } : null;
    },
    [resolveAgentProfileId, resolveAgentTeam]
  );

  const resolveSelectionActiveProfileId = useCallback(
    (
      config: AgentConfig | null,
      selection?: AgentTargetSelection | null,
      desiredActiveProfileId?: string | null
    ) => {
      const resolvedSelection = resolveAgentTargetSelection(config, selection);
      if (resolvedSelection?.kind === 'team') {
        const team = resolveAgentTeam(config, resolvedSelection.id);
        const validActive =
          desiredActiveProfileId &&
          team?.member_profile_ids?.includes(desiredActiveProfileId)
            ? desiredActiveProfileId
            : null;
        if (validActive) return validActive;
        return resolveAgentProfileId(config, team?.leader_profile_id || null);
      }
      const fallbackProfileId = resolvedSelection?.kind === 'profile' ? resolvedSelection.id : null;
      return resolveAgentProfileId(config, desiredActiveProfileId || fallbackProfileId);
    },
    [resolveAgentProfileId, resolveAgentTargetSelection, resolveAgentTeam]
  );

  const loadDefaultConfig = useCallback(async () => {
    try {
      const config = await getDefaultConfig();
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load default config:', error);
      setShowConfigManager(true);
    }
  }, [setCurrentConfig, setShowConfigManager]);

  const loadAllConfigs = useCallback(async () => {
    try {
      const configs = await getConfigs();
      setAllConfigs(configs);
    } catch (error) {
      console.error('Failed to load configs:', error);
    }
  }, [setAllConfigs]);

  const loadAgentConfig = useCallback(async () => {
    try {
      const appConfig = await getAppConfig();
      const agent = (appConfig?.agent || null) as AgentConfig | null;
      setAgentConfig(agent);
      setSelectedAgentTarget((prev) => {
        const nextSelection = resolveAgentTargetSelection(agent, prev);
        setCurrentActiveAgentProfileId((current) =>
          resolveSelectionActiveProfileId(agent, nextSelection, current)
        );
        return nextSelection;
      });
    } catch (error) {
      console.error('Failed to load agent config:', error);
    }
  }, [
    resolveAgentTargetSelection,
    resolveSelectionActiveProfileId,
    setAgentConfig,
    setCurrentActiveAgentProfileId,
    setSelectedAgentTarget,
  ]);

  const loadSkills = useCallback(async () => {
    try {
      const data = await getSkills();
      setSkills(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error('Failed to load skills:', error);
      setSkills([]);
    }
  }, [setSkills]);

  useEffect(() => {
    void loadDefaultConfig();
    void loadAllConfigs();
    void loadAgentConfig();
    void loadSkills();
  }, [loadAgentConfig, loadAllConfigs, loadDefaultConfig, loadSkills]);

  return {
    resolveAgentProfileId,
    resolveAgentTeam,
    resolveAgentTargetSelection,
    resolveSelectionActiveProfileId,
    loadDefaultConfig,
    loadAllConfigs,
    loadAgentConfig,
    loadSkills,
  };
}
