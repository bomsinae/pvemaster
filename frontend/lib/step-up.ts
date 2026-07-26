type StepUpHandler = (action: string) => Promise<string>;

let activeHandler: StepUpHandler | null = null;

export function registerStepUpHandler(handler: StepUpHandler): () => void {
  activeHandler = handler;
  return () => {
    if (activeHandler === handler) activeHandler = null;
  };
}

export function hasStepUpHandler(): boolean {
  return activeHandler !== null;
}

export async function requestStepUp(action: string): Promise<string> {
  if (!activeHandler) throw new Error("Step-up UI is unavailable.");
  return activeHandler(action);
}
