import { useState, useEffect, useRef } from 'react';
import LandingIntro from './features/landing/LandingIntro';
import CapturePhoto from './features/camera/CapturePhoto';
import Questionnaire from './features/questionnaire/Questionnaire';
import RecommendationPage from './features/recommendation/RecommendationPage';
import { useSessionStore } from './store/useSessionStore.js';
import { warmUpServer } from './api';

function App() {
  const currentStep = useSessionStore((state) => state.currentStep);
  const setStep = useSessionStore((state) => state.setStep);

  const [shopifyUser, setShopifyUser] = useState(null);
  const isPopStateNavigation = useRef(false);

  useEffect(() => {
    // Immediately send a silent wake-up ping to the backend on page load
    warmUpServer();

    const widgetRoot = document.getElementById('ai-skin-assessment');

    if (widgetRoot) {
      const { customerFirstName, customerName, customerId, customerEmail } =
        widgetRoot.dataset;

      setShopifyUser({
        firstName: customerFirstName || customerName || null,
        customerId: customerId || null,
        email: customerEmail || null,
      });
    }

    // Seed initial browser history state with the current step
    const initialStep = useSessionStore.getState().currentStep;
    if (!window.history.state?.step) {
      window.history.replaceState(
        { step: initialStep, questionIndex: 0 },
        ''
      );
    }

    const handlePopState = (event) => {
      const targetStep = event.state?.step;
      const activeStep = useSessionStore.getState().currentStep;

      // Once on the final recommendation page, prevent backing into the locked questionnaire
      if (activeStep === 'recommendation' && targetStep !== 'recommendation') {
        window.history.pushState({ step: 'recommendation' }, '');
        return;
      }

      if (targetStep && targetStep !== activeStep) {
        isPopStateNavigation.current = true;
        setStep(targetStep);
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [setStep]);

  // Push a history state whenever the top-level step changes via UI actions
  useEffect(() => {
    if (isPopStateNavigation.current) {
      isPopStateNavigation.current = false;
      return;
    }

    if (window.history.state?.step !== currentStep) {
      window.history.pushState(
        {
          step: currentStep,
          questionIndex: currentStep === 'questionnaire' ? 0 : undefined,
        },
        ''
      );
    }
  }, [currentStep]);

  return (
    <div className="flex min-h-screen w-full flex-col">
      {currentStep === 'landing' && (
        <LandingIntro onStartAssessment={() => setStep('camera')} />
      )}

      {currentStep === 'camera' && (
        <CapturePhoto
          onSubmit={() => {
            setStep('questionnaire');
          }}
        />
      )}

      {currentStep === 'questionnaire' && (
        <Questionnaire
          onComplete={() => {
            setStep('recommendation');
          }}
        />
      )}

      {currentStep === 'recommendation' && (
        <RecommendationPage shopifyUser={shopifyUser} />
      )}
    </div>
  );
}

export default App;