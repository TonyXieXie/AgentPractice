import type { AgentStep } from '../api';

export type PtyOwnerByPtyId = Record<string, string>;

export type PtyOwnerActivation = {
  sessionId?: string | null;
  ptyId: string;
  ownerKey: string;
};

export type PtySendInputParams = {
  sessionId: string;
  ptyId: string;
  input: string;
};

export type PtyControlParams = {
  sessionId: string;
  ptyId: string;
};

export interface PtyInteractionController {
  ownerByPtyId: PtyOwnerByPtyId;
  activateOwner: (params: PtyOwnerActivation) => void;
  sendInput: (params: PtySendInputParams) => Promise<void>;
  flushInput?: (params: PtyControlParams) => Promise<void>;
  continueWithoutInput: (params: PtyControlParams) => Promise<void>;
  closePty: (params: PtyControlParams) => Promise<void>;
}

export interface StepPtyBinding {
  ptyId?: string;
  streamKey?: string;
  ownerKey?: string;
  isLatestRunningStep?: boolean;
  frozenContent?: string;
}

export type ResolveStepPtyBinding = (params: {
  step: AgentStep;
  index: number;
  messageId?: number;
  sessionId?: string;
}) => StepPtyBinding;
