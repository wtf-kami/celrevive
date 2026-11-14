import { useState, useEffect, useRef } from 'react';
import { ArrowRight, ArrowLeft, Loader2, RefreshCcw } from 'lucide-react';
import { submitQuestionnaire } from '../../api';
import { useSessionStore } from '../../store/useSessionStore';
import {
  QUESTIONS,
  TOTAL_QUESTIONS,
  EXCLUSIVE_MULTI_OPTIONS,
  buildCanonicalQuestionnairePayload,
} from '../../constants/questionnaireData';

export default function Questionnaire({ onComplete }) {
  const sessionId = useSessionStore((state) => state.sessionId);
  const clearSession = useSessionStore((state) => state.clearSession);

  const [currentIndex, setCurrentIndex] = useState(() => {
    const savedIdx = window.history.state?.questionIndex;
    return typeof savedIdx === 'number' &&
      savedIdx >= 0 &&
      savedIdx < QUESTIONS.length
      ? savedIdx
      : 0;
  });
  const [answers, setAnswers] = useState({});
  const [additionalDetails, setAdditionalDetails] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  const isQuestionPopState = useRef(false);

  // 1. Sync browser Back/Forward buttons with individual question index
  useEffect(() => {
    if (
      window.history.state?.step !== 'questionnaire' ||
      typeof window.history.state?.questionIndex !== 'number'
    ) {
      window.history.replaceState(
        { step: 'questionnaire', questionIndex: currentIndex },
        ''
      );
    }

    const handleQuestionPopState = (event) => {
      if (
        event.state?.step === 'questionnaire' &&
        typeof event.state.questionIndex === 'number'
      ) {
        isQuestionPopState.current = true;
        setSubmitError('');
        setCurrentIndex(event.state.questionIndex);
      }
    };

    window.addEventListener('popstate', handleQuestionPopState);
    return () => window.removeEventListener('popstate', handleQuestionPopState);
  }, []);

  // 2. Push new history entry whenever the user advances to a new question via UI
  useEffect(() => {
    if (isQuestionPopState.current) {
      isQuestionPopState.current = false;
      return;
    }

    if (
      window.history.state?.step === 'questionnaire' &&
      window.history.state?.questionIndex !== currentIndex
    ) {
      window.history.pushState(
        { step: 'questionnaire', questionIndex: currentIndex },
        ''
      );
    }
  }, [currentIndex]);

  const currentQuestion = QUESTIONS[currentIndex];
  const isMulti = currentQuestion.type === 'multi';
  const isListLayout = currentQuestion.layout === 'list';
  const isWithInput = currentQuestion.type === 'single-with-input';
  const isSingleLine = Boolean(currentQuestion.singleLine);
  const hideCounter = Boolean(currentQuestion.hideCounter);
  const rawAnswer = answers[currentQuestion.id];

  const selectedSingle = !isMulti ? rawAnswer : undefined;
  const selectedMulti = isMulti ? rawAnswer || [] : [];

  const hasSelection = isMulti
    ? selectedMulti.length > 0
    : isWithInput && selectedSingle === 'Yes'
    ? Boolean(additionalDetails.trim())
    : Boolean(selectedSingle);

  const progressPercent = ((currentIndex + 1) / TOTAL_QUESTIONS) * 100;

  const handleSelectSingle = (option) => {
    setSubmitError('');
    setAnswers((prev) => ({
      ...prev,
      [currentQuestion.id]: prev[currentQuestion.id] === option ? undefined : option,
    }));
  };

  const handleSelectMulti = (option) => {
    setSubmitError('');
    setAnswers((prev) => {
      const current = prev[currentQuestion.id] || [];
      const alreadySelected = current.includes(option);

      if (EXCLUSIVE_MULTI_OPTIONS.includes(option)) {
        return { ...prev, [currentQuestion.id]: alreadySelected ? [] : [option] };
      }

      const filteredCurrent = current.filter(
        (o) => !EXCLUSIVE_MULTI_OPTIONS.includes(o)
      );

      if (alreadySelected) {
        return {
          ...prev,
          [currentQuestion.id]: filteredCurrent.filter((o) => o !== option),
        };
      }
      if (filteredCurrent.length >= (currentQuestion.maxSelect || 3)) {
        return prev;
      }
      return { ...prev, [currentQuestion.id]: [...filteredCurrent, option] };
    });
  };

  const handleSelectOption = (option) => {
    if (isMulti) {
      handleSelectMulti(option);
    } else {
      handleSelectSingle(option);
    }
  };

  const handlePrevious = () => {
    if (currentIndex > 0 && !isSubmitting) {
      setSubmitError('');
      // Pop the browser history stack so UI "Previous" and Browser "Back" stay in sync
      if (
        window.history.state?.step === 'questionnaire' &&
        window.history.state?.questionIndex === currentIndex
      ) {
        window.history.back();
      } else {
        setCurrentIndex((prev) => prev - 1);
      }
    }
  };

  const isLastQuestion = currentIndex === TOTAL_QUESTIONS - 1;

  const handleNext = async () => {
    if (!hasSelection || isSubmitting) return;

    if (currentIndex < QUESTIONS.length - 1) {
      setCurrentIndex((prev) => prev + 1);
      return;
    }

    if (isLastQuestion) {
      if (!sessionId) {
        setSubmitError('Your session expired. Please restart the assessment.');
        return;
      }

      setIsSubmitting(true);
      setSubmitError('');
      try {
        const canonicalPayload = buildCanonicalQuestionnairePayload(
          answers,
          additionalDetails
        );
        await submitQuestionnaire(sessionId, canonicalPayload);
        await onComplete?.(canonicalPayload);
      } catch (err) {
        console.error('Questionnaire submission failed:', err);
        const msg = err?.message || 'Unable to submit questionnaire. Please try again.';
        // If questionnaire was already submitted for this session, proceed to recommendation
        if (err?.status === 409 && msg.toLowerCase().includes('already been submitted')) {
          await onComplete?.();
          return;
        }
        setSubmitError(msg);
      } finally {
        setIsSubmitting(false);
      }
    }
  };

  return (
    <div className="flex w-full flex-1 flex-col bg-[var(--bg)]">
      {/* Progress bar + question counter */}
      <div className="w-full shrink-0 border-b border-[var(--border)] px-[24px] py-[14px]">
        <div className="mx-auto flex max-w-[896px] items-center justify-between gap-[12px]">
          <span className="text-[12px] font-medium text-[var(--text)]">
            {currentQuestion.section_title}
          </span>
          <span className="text-[12px] font-semibold tracking-widest text-[var(--text)]">
            QUESTION {currentIndex + 1} OUT OF {TOTAL_QUESTIONS}
          </span>
        </div>
        <div className="mx-auto mt-[8px] h-[6px] w-full max-w-[896px] overflow-hidden rounded-full bg-[var(--code-bg)]">
          <div
            className="h-full rounded-full bg-[var(--text-h)] transition-all duration-300"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Heading + Options (Natural page scroll, explicit px sizing for Shopify) */}
      <div className="flex flex-1 flex-col items-center justify-center px-[20px] py-[32px]">
        <div
          className={`mb-[20px] flex flex-col items-center justify-center text-center ${
            isSingleLine ? 'max-w-[1024px]' : 'max-w-[768px]'
          }`}
        >
          <h1 className="m-0 text-[20px] font-bold leading-snug text-[var(--text-h)] sm:text-[24px] md:text-[26px]">
            {currentQuestion.displayQuestion || currentQuestion.question}
            {currentQuestion.subtitle && (
              <span className="ml-[8px] font-bold italic text-[var(--text-h)]">
                {currentQuestion.subtitle}
              </span>
            )}
          </h1>
        </div>

        {submitError && (
          <div className="mb-[20px] flex w-full max-w-[576px] flex-col items-center gap-[10px] rounded-xl border border-red-200 bg-red-50 px-[16px] py-[12px] text-center">
            <p className="text-[13px] font-medium text-red-600">{submitError}</p>
            <button
              type="button"
              onClick={clearSession}
              className="inline-flex items-center gap-[6px] rounded-lg bg-red-600 px-[14px] py-[6px] text-[12px] font-semibold text-white hover:bg-red-700"
            >
              <RefreshCcw className="h-[14px] w-[14px]" /> Restart Assessment
            </button>
          </div>
        )}

        {isMulti && (
          <div
            className={`mb-[16px] flex w-full max-w-[896px] items-center px-[8px] text-[13px] ${
              hideCounter ? 'justify-end' : 'justify-between'
            }`}
          >
            <span className="font-medium text-[var(--text)]">
              {hideCounter
                ? '(Multi-Select Answers)'
                : `(Select up to ${currentQuestion.maxSelect})`}
            </span>
            {!hideCounter && currentQuestion.maxSelect && (
              <span className="font-semibold uppercase tracking-wider text-[var(--text-h)]">
                SELECTED: {selectedMulti.length}/{currentQuestion.maxSelect}
              </span>
            )}
          </div>
        )}

        {/* Dynamic Grid / Stack based on layout with explicit px widths */}
        <div
          className={`mx-auto w-full ${
            isListLayout
              ? 'max-w-[576px] space-y-[12px]'
              : currentQuestion.cols === 3
              ? 'grid max-w-[896px] grid-cols-1 gap-[12px] sm:grid-cols-2 md:grid-cols-3'
              : 'grid max-w-[896px] grid-cols-2 gap-[12px] md:grid-cols-4'
          }`}
        >
          {currentQuestion.options.map((option) => {
            const isSelected = isMulti
              ? selectedMulti.includes(option)
              : selectedSingle === option;
            return (
              <button
                key={option}
                type="button"
                onClick={() => handleSelectOption(option)}
                className={`flex items-center rounded-xl border px-[20px] text-[14px] font-medium transition-all ${
                  isListLayout
                    ? 'min-h-[54px] w-full justify-start py-[12px] text-left'
                    : 'min-h-[76px] justify-center py-[14px] text-center md:min-h-[88px]'
                } ${
                  isSelected
                    ? 'border-[var(--text-h)] bg-[var(--text-h)] text-[var(--bg)] shadow-sm'
                    : 'border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] hover:bg-[var(--code-bg)]'
                }`}
              >
                {option}
              </button>
            );
          })}

          {/* Conditional Text Area for Q7 ("If Yes") */}
          {isWithInput && selectedSingle === 'Yes' && (
            <div className="mt-[16px] w-full text-left">
              <label className="mb-[8px] block text-[13px] font-semibold text-[var(--text-h)]">
                Please provide details (Required)
              </label>
              <textarea
                value={additionalDetails}
                onChange={(e) => setAdditionalDetails(e.target.value)}
                placeholder="List any allergies or ingredient sensitivities..."
                maxLength={1000}
                className="h-[112px] w-full resize-none rounded-xl border border-[var(--border)] bg-[var(--bg)] p-[14px] text-[14px] text-[var(--text-h)] focus:border-[var(--text-h)] focus:outline-none"
              />
            </div>
          )}
        </div>
      </div>

      {/* Bottom Navigation Bar */}
      <div className="w-full shrink-0 border-t border-[var(--border)] px-[24px] py-[16px]">
        <div className="mx-auto flex max-w-[896px] items-center justify-between">
          {currentIndex > 0 ? (
            <button
              type="button"
              disabled={isSubmitting}
              onClick={handlePrevious}
              className="flex items-center gap-[8px] rounded-full border border-[var(--border)] px-[20px] py-[10px] text-[13px] font-bold uppercase tracking-wide text-[var(--text-h)] transition-colors hover:bg-[var(--code-bg)] disabled:opacity-50"
            >
              <ArrowLeft className="h-[16px] w-[16px]" strokeWidth={2.5} />
              Previous
            </button>
          ) : (
            <div />
          )}

          <button
            type="button"
            onClick={handleNext}
            disabled={!hasSelection || isSubmitting}
            className={`flex items-center gap-[8px] rounded-full px-[24px] py-[10px] text-[13px] font-bold uppercase tracking-wide transition-all ${
              hasSelection && !isSubmitting
                ? 'bg-[var(--text-h)] text-[var(--bg)] hover:opacity-90'
                : 'cursor-not-allowed bg-[var(--code-bg)] text-[var(--text)] opacity-50'
            }`}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-[16px] w-[16px] animate-spin" />
                Submitting...
              </>
            ) : isLastQuestion ? (
              <>
                Submit
                <ArrowRight className="h-[16px] w-[16px]" strokeWidth={2.5} />
              </>
            ) : (
              <>
                Next
                <ArrowRight className="h-[16px] w-[16px]" strokeWidth={2.5} />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}