// CUI // SP-CTI
// AuditContract — store and verify Merkle roots for audit trail batches
package main

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// MerkleRootRecord stores an anchored Merkle root
type MerkleRootRecord struct {
	Root       string `json:"root"`
	SourceTable string `json:"source_table"`
	BatchSize  int    `json:"batch_size"`
	FirstID    string `json:"first_id"`
	LastID     string `json:"last_id"`
	Timestamp  string `json:"timestamp"`
	TxID       string `json:"tx_id"`
}

// AuditContract provides chaincode functions for audit trail integrity
type AuditContract struct {
	contractapi.Contract
}

// StoreMerkleRoot anchors a Merkle root for an audit batch
func (c *AuditContract) StoreMerkleRoot(ctx contractapi.TransactionContextInterface, root string, sourceTable string, batchSize int, firstID string, lastID string) error {
	if root == "" {
		return fmt.Errorf("root cannot be empty")
	}

	key := fmt.Sprintf("merkle:%s", root)
	existing, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if existing != nil {
		return fmt.Errorf("merkle root %s already anchored", root)
	}

	record := MerkleRootRecord{
		Root:        root,
		SourceTable: sourceTable,
		BatchSize:   batchSize,
		FirstID:     firstID,
		LastID:      lastID,
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		TxID:        ctx.GetStub().GetTxID(),
	}

	recordJSON, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}

	return ctx.GetStub().PutState(key, recordJSON)
}

// VerifyMerkleRoot checks if a Merkle root has been anchored
func (c *AuditContract) VerifyMerkleRoot(ctx contractapi.TransactionContextInterface, root string) (bool, error) {
	if root == "" {
		return false, fmt.Errorf("root cannot be empty")
	}

	key := fmt.Sprintf("merkle:%s", root)
	recordJSON, err := ctx.GetStub().GetState(key)
	if err != nil {
		return false, fmt.Errorf("failed to read state: %v", err)
	}

	return recordJSON != nil, nil
}

// GetLatestRoot returns the most recent Merkle root for a source table
func (c *AuditContract) GetLatestRoot(ctx contractapi.TransactionContextInterface, sourceTable string) (string, error) {
	// Query by partial key using rich query (requires CouchDB state database)
	query := fmt.Sprintf(`{"selector":{"source_table":"%s"}}`, sourceTable)
	resultsIterator, err := ctx.GetStub().GetQueryResult(query)
	if err != nil {
		return "", fmt.Errorf("failed to query: %v", err)
	}
	defer resultsIterator.Close()

	var latest MerkleRootRecord
	var found bool
	for resultsIterator.HasNext() {
		response, err := resultsIterator.Next()
		if err != nil {
			continue
		}
		var record MerkleRootRecord
		if err := json.Unmarshal(response.Value, &record); err != nil {
			continue
		}
		if !found || record.Timestamp > latest.Timestamp {
			latest = record
			found = true
		}
	}

	if !found {
		return "", fmt.Errorf("no merkle roots found for table %s", sourceTable)
	}

	return latest.Root, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&AuditContract{})
	if err != nil {
		fmt.Printf("Error creating audit chaincode: %v", err)
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting audit chaincode: %v", err)
	}
}
