// CUI // SP-CTI
// EvidenceContract — store and verify compliance evidence hashes
package main

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// EvidenceRecord stores a compliance evidence hash
type EvidenceRecord struct {
	EvidenceID string `json:"evidence_id"`
	Hash       string `json:"hash"`
	OSCALFamily string `json:"oscal_family"`
	Timestamp  string `json:"timestamp"`
	TxID       string `json:"tx_id"`
}

// EvidenceContract provides chaincode for compliance evidence tracking
type EvidenceContract struct {
	contractapi.Contract
}

// StoreEvidenceHash anchors a compliance evidence hash
func (c *EvidenceContract) StoreEvidenceHash(ctx contractapi.TransactionContextInterface, evidenceID string, hash string, oscalFamily string) error {
	if evidenceID == "" || hash == "" {
		return fmt.Errorf("evidence_id and hash cannot be empty")
	}

	key := fmt.Sprintf("evidence:%s", evidenceID)
	existing, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if existing != nil {
		return fmt.Errorf("evidence %s already exists", evidenceID)
	}

	record := EvidenceRecord{
		EvidenceID:  evidenceID,
		Hash:        hash,
		OSCALFamily: oscalFamily,
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		TxID:        ctx.GetStub().GetTxID(),
	}

	recordJSON, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}

	return ctx.GetStub().PutState(key, recordJSON)
}

// VerifyEvidenceHash checks if an evidence hash matches the stored value
func (c *EvidenceContract) VerifyEvidenceHash(ctx contractapi.TransactionContextInterface, evidenceID string, hash string) (bool, error) {
	if evidenceID == "" || hash == "" {
		return false, fmt.Errorf("evidence_id and hash cannot be empty")
	}

	key := fmt.Sprintf("evidence:%s", evidenceID)
	recordJSON, err := ctx.GetStub().GetState(key)
	if err != nil {
		return false, fmt.Errorf("failed to read state: %v", err)
	}
	if recordJSON == nil {
		return false, nil
	}

	var record EvidenceRecord
	if err := json.Unmarshal(recordJSON, &record); err != nil {
		return false, fmt.Errorf("failed to unmarshal record: %v", err)
	}

	return record.Hash == hash, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&EvidenceContract{})
	if err != nil {
		fmt.Printf("Error creating evidence chaincode: %v", err)
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting evidence chaincode: %v", err)
	}
}
