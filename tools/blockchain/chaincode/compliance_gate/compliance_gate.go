package main

import (
	"encoding/json"
	"fmt"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// ComplianceGateContract evaluates whether a project meets a compliance gate
// by verifying that all required evidence hashes are anchored on the ledger.
type ComplianceGateContract struct {
	contractapi.Contract
}

// EvidenceRequirement defines a single control and its required evidence hashes.
type EvidenceRequirement struct {
	ControlID     string   `json:"control_id"`
	Framework     string   `json:"framework"`     // e.g. NIST-800-53, CMMC, FedRAMP
	Family        string   `json:"family"`        // e.g. AU, AC, SC
	RequiredHashes []string `json:"required_hashes"`
}

// GateEvaluationResult is the output of EvaluateGate.
type GateEvaluationResult struct {
	ProjectID     string            `json:"project_id"`
	GateID        string            `json:"gate_id"`
	Passed        bool              `json:"passed"`
	Justification string            `json:"justification"`
	Missing       []string          `json:"missing_hashes"`
	Verified      []string          `json:"verified_hashes"`
	EvaluatedAt   string            `json:"evaluated_at"`
}

// EvaluateGate checks that every required evidence hash for a given project
// and gate ID exists on the ledger (i.e. has been anchored).
//
// Args:
//   - projectID   (string)
//   - gateID      (string)
//   - requirements ([]EvidenceRequirement as JSON string)
//
// Returns GateEvaluationResult JSON.
func (c *ComplianceGateContract) EvaluateGate(ctx contractapi.TransactionContextInterface, projectID string, gateID string, requirementsJSON string) (string, error) {
	timestamp, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return "", fmt.Errorf("failed to get tx timestamp: %w", err)
	}

	var requirements []EvidenceRequirement
	if err := json.Unmarshal([]byte(requirementsJSON), &requirements); err != nil {
		return "", fmt.Errorf("invalid requirements JSON: %w", err)
	}

	result := GateEvaluationResult{
		ProjectID:   projectID,
		GateID:      gateID,
		Passed:      true,
		EvaluatedAt: timestamp.String(),
	}

	for _, req := range requirements {
		for _, hash := range req.RequiredHashes {
			// Check if hash exists in the evidence contract state
			key := fmt.Sprintf("EVIDENCE|%s", hash)
			data, err := ctx.GetStub().GetState(key)
			if err != nil {
				return "", fmt.Errorf("ledger read failed for hash %s: %w", hash, err)
			}
			if data == nil {
				result.Passed = false
				result.Missing = append(result.Missing, hash)
			} else {
				result.Verified = append(result.Verified, hash)
			}
		}
	}

	if result.Passed {
		result.Justification = fmt.Sprintf("All %d required evidence hashes are anchored on the ledger.", len(result.Verified))
	} else {
		result.Justification = fmt.Sprintf("Missing %d of %d required evidence hashes.",
			len(result.Missing), len(result.Missing)+len(result.Verified))
	}

	// Persist evaluation result for audit
	resultKey := fmt.Sprintf("GATE|%s|%s", projectID, gateID)
	resultBytes, _ := json.Marshal(result)
	if err := ctx.GetStub().PutState(resultKey, resultBytes); err != nil {
		return "", fmt.Errorf("failed to persist gate result: %w", err)
	}

	return string(resultBytes), nil
}

// GetGateResult retrieves a previously stored gate evaluation.
func (c *ComplianceGateContract) GetGateResult(ctx contractapi.TransactionContextInterface, projectID string, gateID string) (string, error) {
	key := fmt.Sprintf("GATE|%s|%s", projectID, gateID)
	data, err := ctx.GetStub().GetState(key)
	if err != nil {
		return "", err
	}
	if data == nil {
		return "", fmt.Errorf("gate result not found for project=%s gate=%s", projectID, gateID)
	}
	return string(data), nil
}

// GetHistory returns the transaction history for a gate result.
func (c *ComplianceGateContract) GetHistory(ctx contractapi.TransactionContextInterface, projectID string, gateID string) (string, error) {
	key := fmt.Sprintf("GATE|%s|%s", projectID, gateID)
	iterator, err := ctx.GetStub().GetHistoryForKey(key)
	if err != nil {
		return "", err
	}
	defer iterator.Close()

	var history []map[string]interface{}
	for iterator.HasNext() {
		response, err := iterator.Next()
		if err != nil {
			return "", err
		}
		history = append(history, map[string]interface{}{
			"tx_id":      response.TxId,
			"value":      string(response.Value),
			"timestamp":  response.Timestamp.String(),
			"is_delete":  response.IsDelete,
		})
	}

	bytes, _ := json.Marshal(history)
	return string(bytes), nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(new(ComplianceGateContract))
	if err != nil {
		panic(fmt.Sprintf("Error creating compliance_gate chaincode: %v", err))
	}
	if err := chaincode.Start(); err != nil {
		panic(fmt.Sprintf("Error starting compliance_gate chaincode: %v", err))
	}
}
