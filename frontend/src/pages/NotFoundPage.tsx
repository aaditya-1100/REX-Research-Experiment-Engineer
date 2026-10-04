import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "../components/ui/Button";

export const NotFoundPage: React.FC = () => {
  return (
    <div className="py-24 text-center space-y-4">
      <div className="font-mono text-3xl font-bold text-rex-primary">404</div>
      <h2 className="text-base font-semibold text-rex-primary">Resource Not Found</h2>
      <p className="text-xs text-rex-secondary max-w-sm mx-auto">
        The requested research workspace path does not exist in the active provenance plane.
      </p>
      <div className="pt-2">
        <Link to="/">
          <Button variant="secondary" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />}>
            Return to Command Center
          </Button>
        </Link>
      </div>
    </div>
  );
};
