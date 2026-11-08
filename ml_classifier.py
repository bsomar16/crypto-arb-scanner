#!/usr/bin/env python3
"""ML Signal Classifier for win rate prediction.

Uses XGBoost/LightGBM to predict P(WIN) from signal features.
Retrains weekly on accumulated signal_history.jsonl outcomes.
"""

from __future__ import annotations

import json
import os
import pickle
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    xgb = None

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
    lgb = None

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from botutil import log, load_json, save_json

warnings.filterwarnings("ignore", category=UserWarning)

MODEL_PATH = "state/ml_classifier.pkl"
SCALER_PATH = "state/ml_scaler.pkl"
METADATA_PATH = "state/ml_metadata.json"
TRAINING_LOG_PATH = "state/ml_training_log.jsonl"

# Feature columns (must match extract_features output)
FEATURE_COLUMNS = [
    # Price action
    "close", "rsi", "macd", "macd_signal", "macd_hist",
    "ema_9", "ema_20", "ema_50", "atr", "atr_pct",
    # Volume
    "volume", "volume_ma", "vol_ratio", "vol_acceleration",
    # Structure
    "structure_score", "bos", "choch", "higher_lows", "liquidity_sweep",
    "compression", "distance_from_high_pct",
    # Trend
    "trend_4h_score", "trend_1h_score", "mtf_bullish_count",
    # Setup
    "setup_type_encoded", "entry_quality", "expansion_score",
    "expansion_state_encoded",
    # Context
    "chg24", "chg24_rank", "market_regime_encoded",
    "bullish_potential_score", "bullish_potential_tier_encoded",
    # Derivatives
    "funding_rate", "funding_apy", "basis", "open_interest",
    "oi_change_24h", "liquidation_ratio",
    # Session
    "hour_of_day", "day_of_week", "is_asian_session", "is_eu_session", "is_us_session",
    # Target
    "potential_pct", "rr", "risk_pct"
]

SETUP_TYPE_MAP = {
    "BREAKOUT": 0, "MOMENTUM": 1, "PULLBACK": 2, "REVERSAL": 3, "UNKNOWN": 4
}

EXPANSION_STATE_MAP = {
    "BASE": 0, "EARLY_EXPANSION": 1, "EXPANSION": 2, "LATE_EXTENSION": 3
}

REGIME_MAP = {
    "BULLISH": 2, "MIXED": 1, "BEARISH": 0, "UNKNOWN": 1
}

BULLISH_TIER_MAP = {
    "STANDARD": 0, "BULLISH_CONTINUATION": 1, 
    "STRONG_CONTINUATION": 2, "EXPLOSIVE_CONTINUATION": 3
}


class MLClassifier:
    """ML-based signal win probability classifier."""
    
    def __init__(self, model_type: str = "xgboost", config: Dict = None):
        self.config = config or {}
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.feature_importance = {}
        self.metadata = {
            "model_type": model_type,
            "trained_at": None,
            "train_samples": 0,
            "val_accuracy": 0.0,
            "val_precision": 0.0,
            "val_recall": 0.0,
            "val_auc": 0.0,
            "feature_count": 0
        }
        self.is_trained = False
        
        # Load existing model if available
        self._load_model()
    
    def _load_model(self):
        """Load model from disk."""
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                with open(MODEL_PATH, "rb") as f:
                    self.model = pickle.load(f)
                with open(SCALER_PATH, "rb") as f:
                    self.scaler = pickle.load(f)
                if os.path.exists(METADATA_PATH):
                    with open(METADATA_PATH, "r") as f:
                        self.metadata = json.load(f)
                self.is_trained = True
                log("ML", f"Loaded {self.model_type} model from {MODEL_PATH}")
            except Exception as e:
                log("ML", f"Failed to load model: {e}")
                self.model = None
    
    def _save_model(self):
        """Save model to disk."""
        try:
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            with open(MODEL_PATH, "wb") as f:
                pickle.dump(self.model, f)
            with open(SCALER_PATH, "wb") as f:
                pickle.dump(self.scaler, f)
            self.metadata["trained_at"] = datetime.now(timezone.utc).isoformat()
            with open(METADATA_PATH, "w") as f:
                json.dump(self.metadata, f, indent=2)
            log("ML", f"Saved {self.model_type} model to {MODEL_PATH}")
        except Exception as e:
            log("ML", f"Failed to save model: {e}")
    
    def _create_model(self):
        """Create new model instance."""
        if self.model_type == "xgboost" and XGB_AVAILABLE:
            return xgb.XGBClassifier(
                n_estimators=300,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                gamma=0.1,
                reg_alpha=0.1,
                reg_lambda=1.0,
                objective="binary:logistic",
                eval_metric="auc",
                random_state=42,
                n_jobs=-1,
                verbosity=0
            )
        elif self.model_type == "lightgbm" and LGB_AVAILABLE:
            return lgb.LGBMClassifier(
                n_estimators=300,
                max_depth=6,
                learning_rate=0.05,
                num_leaves=31,
                min_child_samples=20,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.1,
                reg_lambda=1.0,
                objective="binary",
                metric="auc",
                random_state=42,
                n_jobs=-1,
                verbosity=-1
            )
        else:
            # Fallback: sklearn RandomForest
            from sklearn.ensemble import RandomForestClassifier
            return RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                min_samples_split=10,
                min_samples_leaf=5,
                random_state=42,
                n_jobs=-1
            )
    
    def extract_features(self, signal: Dict) -> Optional[np.ndarray]:
        """Extract feature vector from signal dict."""
        try:
            features = {}
            
            # Price action
            features["close"] = float(signal.get("entry", signal.get("price", 0)))
            features["rsi"] = float(signal.get("rsi", 50))
            
            # MACD
            macd = signal.get("macd", 0)
            macd_signal = signal.get("macd_signal", 0)
            features["macd"] = float(macd)
            features["macd_signal"] = float(macd_signal)
            features["macd_hist"] = float(macd - macd_signal)
            
            # EMAs
            features["ema_9"] = float(signal.get("ema_9", features["close"]))
            features["ema_20"] = float(signal.get("ema_20", features["close"]))
            features["ema_50"] = float(signal.get("ema_50", features["close"]))
            
            # ATR
            atr = float(signal.get("atr", 0))
            features["atr"] = atr
            features["atr_pct"] = (atr / features["close"] * 100) if features["close"] > 0 else 0
            
            # Volume
            features["volume"] = float(signal.get("volume", 0))
            features["volume_ma"] = float(signal.get("volume_ma", features["volume"]))
            vol_ratio = float(signal.get("vol_x", 1.0))
            features["vol_ratio"] = vol_ratio
            features["vol_acceleration"] = float(signal.get("vol_acceleration", 1.0))
            
            # Structure
            features["structure_score"] = float(signal.get("structure_score", 0))
            features["bos"] = 1.0 if signal.get("bos", False) else 0.0
            features["choch"] = 1.0 if signal.get("choch", False) else 0.0
            features["higher_lows"] = 1.0 if signal.get("higher_lows", False) else 0.0
            features["liquidity_sweep"] = 1.0 if signal.get("liquidity_sweep", False) else 0.0
            features["compression"] = float(signal.get("compression", 0))
            
            pump_details = signal.get("pump_details", {})
            features["distance_from_high_pct"] = float(pump_details.get("distance_from_high_pct", 0))
            
            # Trend
            trend_4h = signal.get("trend_4h", "MIXED")
            trend_1h = signal.get("mtf_1h", "MIXED")
            features["trend_4h_score"] = float({"BULLISH": 2, "MIXED": 1, "BEARISH": 0}.get(trend_4h, 1))
            features["trend_1h_score"] = float({"BULLISH": 2, "MIXED": 1, "BEARISH": 0}.get(trend_1h, 1))
            
            mtf_fields = ["mtf_1h", "mtf_4h", "mtf_1d", "mtf_1w"]
            mtf_bullish = sum(1 for f in mtf_fields if signal.get(f) == "BULLISH")
            features["mtf_bullish_count"] = float(mtf_bullish)
            
            # Setup
            setup_type = signal.get("setup_type", "UNKNOWN")
            features["setup_type_encoded"] = float(SETUP_TYPE_MAP.get(setup_type, 4))
            features["entry_quality"] = float(signal.get("entry_quality", 50))
            features["expansion_score"] = float(signal.get("expansion_score", 0))
            
            exp_state = signal.get("expansion_state", "BASE")
            features["expansion_state_encoded"] = float(EXPANSION_STATE_MAP.get(exp_state, 0))
            
            # Context
            features["chg24"] = float(signal.get("chg24", 0))
            
            # Market regime
            regime = signal.get("market_regime", "UNKNOWN")
            features["market_regime_encoded"] = float(REGIME_MAP.get(regime, 1))
            
            # Bullish potential
            bp = signal.get("bullish_potential", {})
            features["bullish_potential_score"] = float(bp.get("score", 50))
            bp_tier = bp.get("tier", "STANDARD")
            features["bullish_potential_tier_encoded"] = float(BULLISH_TIER_MAP.get(bp_tier, 0))
            
            # Derivatives
            features["funding_rate"] = float(signal.get("funding_rate", 0))
            features["funding_apy"] = float(signal.get("funding_apy", 0))
            features["basis"] = float(signal.get("basis", 0))
            features["open_interest"] = float(signal.get("open_interest", 0))
            features["oi_change_24h"] = float(signal.get("oi_change_24h", 0))
            features["liquidation_ratio"] = float(signal.get("liquidation_ratio", 0))
            
            # Session
            ts = signal.get("candle_open_time", int(datetime.now(timezone.utc).timestamp()))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            features["hour_of_day"] = float(dt.hour)
            features["day_of_week"] = float(dt.weekday())
            features["is_asian_session"] = 1.0 if 0 <= dt.hour < 8 else 0.0
            features["is_eu_session"] = 1.0 if 8 <= dt.hour < 16 else 0.0
            features["is_us_session"] = 1.0 if 16 <= dt.hour < 24 else 0.0
            
            # Target-related (for training)
            features["potential_pct"] = float(signal.get("potential_pct", 0))
            features["rr"] = float(signal.get("rr", 0))
            features["risk_pct"] = float(signal.get("risk_pct", 0))
            
            # Build feature vector in correct order
            vec = []
            for col in FEATURE_COLUMNS:
                vec.append(features.get(col, 0.0))
            
            return np.array(vec, dtype=np.float32).reshape(1, -1)
            
        except Exception as e:
            log("ML", f"Feature extraction error: {e}")
            return None
    
    def prepare_training_data(self, signal_history_path: str = "state/signal_history.jsonl") -> Tuple[np.ndarray, np.ndarray]:
        """Prepare training data from signal history."""
        X, y = [], []
        
        try:
            signals_by_id = {}
            outcomes_by_id = {}
            
            with open(signal_history_path, "r") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        if record.get("kind") == "signal":
                            signals_by_id[record.get("id")] = record
                        elif record.get("kind") == "outcome":
                            outcomes_by_id[record.get("id")] = record
                    except json.JSONDecodeError:
                        continue
            
            # Match signals with outcomes
            for sig_id, signal in signals_by_id.items():
                outcome = outcomes_by_id.get(sig_id)
                if not outcome:
                    continue
                
                outcome_val = outcome.get("outcome", "").upper()
                if outcome_val not in ("WIN", "LOSS"):
                    continue
                
                feat_vec = self.extract_features(signal)
                if feat_vec is not None:
                    X.append(feat_vec.flatten())
                    y.append(1 if outcome_val == "WIN" else 0)
            
            log("ML", f"Prepared {len(X)} training samples ({sum(y)} wins, {len(y)-sum(y)} losses)")
            return np.array(X), np.array(y)
            
        except Exception as e:
            log("ML", f"Training data preparation error: {e}")
            return np.array([]), np.array([])
    
    def train(self, X: np.ndarray, y: np.ndarray, 
              validation_split: float = 0.2) -> Dict:
        """Train the model with time-series cross-validation."""
        if len(X) < 50:
            return {"error": "Insufficient training data (min 50 samples)"}
        
        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        
        # Time-series split (no shuffle)
        n_splits = 5
        tscv = TimeSeriesSplit(n_splits=n_splits)
        
        # Create model
        self.model = self._create_model()
        
        cv_scores = {"accuracy": [], "precision": [], "recall": [], "auc": []}
        
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
            X_train, X_val = X_scaled[train_idx], X_scaled[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            
            # Train
            if self.model_type in ("xgboost", "lightgbm"):
                self.model.fit(
                    X_train, y_train,
                    eval_set=[(X_val, y_val)],
                    verbose=False
                )
            else:
                self.model.fit(X_train, y_train)
            
            # Evaluate
            y_pred = self.model.predict(X_val)
            y_proba = self.model.predict_proba(X_val)[:, 1]
            
            cv_scores["accuracy"].append(accuracy_score(y_val, y_pred))
            cv_scores["precision"].append(precision_score(y_val, y_pred, zero_division=0))
            cv_scores["recall"].append(recall_score(y_val, y_pred, zero_division=0))
            cv_scores["auc"].append(roc_auc_score(y_val, y_proba))
        
        # Final fit on all data
        self.model.fit(X_scaled, y)
        
        # Feature importance
        if hasattr(self.model, "feature_importances_"):
            self.feature_importance = dict(zip(FEATURE_COLUMNS, self.model.feature_importances_))
        
        # Update metadata
        self.metadata.update({
            "train_samples": len(X),
            "val_accuracy": float(np.mean(cv_scores["accuracy"])),
            "val_precision": float(np.mean(cv_scores["precision"])),
            "val_recall": float(np.mean(cv_scores["recall"])),
            "val_auc": float(np.mean(cv_scores["auc"])),
            "feature_count": X.shape[1],
            "cv_folds": n_splits,
            "model_type": self.model_type
        })
        
        self.is_trained = True
        self._save_model()
        
        log("ML", f"Training complete: AUC={self.metadata['val_auc']:.3f}, "
            f"Precision={self.metadata['val_precision']:.3f}, "
            f"Recall={self.metadata['val_recall']:.3f}")
        
        return {
            "cv_accuracy": cv_scores["accuracy"],
            "cv_precision": cv_scores["precision"],
            "cv_recall": cv_scores["recall"],
            "cv_auc": cv_scores["auc"],
            "mean_accuracy": self.metadata["val_accuracy"],
            "mean_precision": self.metadata["val_precision"],
            "mean_recall": self.metadata["val_recall"],
            "mean_auc": self.metadata["val_auc"]
        }
    
    def predict_proba(self, signal: Dict) -> float:
        """Predict P(WIN) for a signal."""
        if not self.is_trained or self.model is None:
            return 0.5
        
        feat_vec = self.extract_features(signal)
        if feat_vec is None:
            return 0.5
        
        try:
            feat_scaled = self.scaler.transform(feat_vec)
            proba = self.model.predict_proba(feat_scaled)[0, 1]
            return float(proba)
        except Exception as e:
            log("ML", f"Prediction error: {e}")
            return 0.5
    
    def predict(self, signal: Dict, threshold: float = 0.55) -> int:
        """Predict WIN (1) or LOSS (0)."""
        proba = self.predict_proba(signal)
        return 1 if proba >= threshold else 0
    
    def get_feature_importance(self, top_n: int = 20) -> List[Tuple[str, float]]:
        """Get top N most important features."""
        if not self.feature_importance:
            return []
        sorted_feats = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)
        return sorted_feats[:top_n]
    
    def retrain_if_needed(self, signal_history_path: str = "state/signal_history.jsonl",
                          min_new_samples: int = 20) -> bool:
        """Check if retraining is needed and retrain."""
        try:
            # Count new outcomes since last training
            last_trained = self.metadata.get("trained_at")
            if not last_trained:
                return self._full_retrain(signal_history_path)
            
            last_dt = datetime.fromisoformat(last_trained.replace("Z", "+00:00"))
            new_outcomes = 0
            
            with open(signal_history_path, "r") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        if record.get("kind") == "outcome":
                            ts = datetime.fromisoformat(record.get("ts", "").replace("Z", "+00:00"))
                            if ts > last_dt:
                                new_outcomes += 1
                    except:
                        continue
            
            if new_outcomes >= min_new_samples:
                log("ML", f"Retraining triggered: {new_outcomes} new outcomes")
                return self._full_retrain(signal_history_path)
            
            return False
            
        except Exception as e:
            log("ML", f"Retrain check error: {e}")
            return False
    
    def _full_retrain(self, signal_history_path: str) -> bool:
        """Full retrain from scratch."""
        X, y = self.prepare_training_data(signal_history_path)
        if len(X) >= 50:
            self.train(X, y)
            return True
        return False


def get_ml_classifier(config: Dict = None) -> MLClassifier:
    """Factory function to get configured ML classifier."""
    model_type = config.get("ml_model_type", "xgboost") if config else "xgboost"
    
    # Auto-select available model
    if model_type == "xgboost" and not XGB_AVAILABLE:
        if LGB_AVAILABLE:
            model_type = "lightgbm"
        else:
            model_type = "randomforest"
    elif model_type == "lightgbm" and not LGB_AVAILABLE:
        if XGB_AVAILABLE:
            model_type = "xgboost"
        else:
            model_type = "randomforest"
    
    return MLClassifier(model_type=model_type, config=config)


# Integration function for scanner
def enhance_signal_with_ml(signal: Dict, classifier: MLClassifier, 
                           threshold: float = 0.55) -> Dict:
    """Add ML prediction to signal."""
    if not classifier.is_trained:
        signal["ml_win_prob"] = 0.5
        signal["ml_prediction"] = "UNKNOWN"
        return signal
    
    prob = classifier.predict_proba(signal)
    pred = classifier.predict(signal, threshold)
    
    signal["ml_win_prob"] = round(prob, 3)
    signal["ml_prediction"] = "WIN" if pred == 1 else "LOSS"
    signal["ml_threshold"] = threshold
    
    # Boost/reject based on ML
    if prob >= threshold:
        signal["ml_boost"] = min(10, (prob - threshold) * 50)
    else:
        signal["ml_boost"] = -min(15, (threshold - prob) * 50)
    
    return signal