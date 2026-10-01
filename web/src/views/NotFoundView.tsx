import React from 'react';
import { Link } from 'react-router-dom';
import { TactileButton } from '../components/ui/TactileButton';

export const NotFoundView: React.FC = () => {
  return (
    <div className="py-24 text-center space-y-6 font-interface">
      <div className="font-machine text-xs tracking-widest text-grey-500 uppercase">
        HTTP 404 // OBJECT UNRESOLVED
      </div>
      <h1 className="font-display text-5xl text-pure tracking-tight">
        Dossier Not Located
      </h1>
      <p className="text-sm text-grey-300 max-w-md mx-auto">
        The requested record or pathway does not exist within this operational scope,
        or has been removed in accordance with access policy.
      </p>
      <div className="pt-4">
        <Link to="/">
          <TactileButton variant="primary" size="md">
            Return to Index →
          </TactileButton>
        </Link>
      </div>
    </div>
  );
};
