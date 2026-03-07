import { useCallback, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import { getAppConfig, getConfigs, getDefaultConfig, getSkills } from '../../shared/api';
import type { AgentConfig, LLMConfig, SkillSummary } from '../../types';

type UseAppShellBootstrapParams = {
  setCurrentConfig: Dispatch<SetStateAction<LLMConfig | null>>;
  setShowConfigManager: Dispatch<SetStateAction<boolean>>;
  setAllConfigs: Dispatch<SetStateAction<LLMConfig[]>>;
  setAgentConfig: Dispatch<SetStateAction<AgentConfig | null>>;
  setCurrentAgentProfileId: Dispatch<SetStateAction<string | null>>;
  setSkills: Dispatch<SetStateAction<SkillSummary[]>>;
};

export function useAppShellBootstrap({
  setCurrentConfig,
  setShowConfigManager,
  setAllConfigs,
  setAgentConfig,
  setCurrentAgentProfileId,
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
      setCurrentAgentProfileId((prev) => resolveAgentProfileId(agent, prev));
    } catch (error) {
      console.error('Failed to load agent config:', error);
    }
  }, [resolveAgentProfileId, setAgentConfig, setCurrentAgentProfileId]);

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
    loadDefaultConfig,
    loadAllConfigs,
    loadAgentConfig,
    loadSkills,
  };
}
