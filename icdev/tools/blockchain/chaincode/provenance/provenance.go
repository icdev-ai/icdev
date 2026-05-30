// CUI // SP-CTI
// ProvenanceContract — store and verify provenance hashes on Hyperledger Fabric
package main

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// ProvenanceRecord stores a single hash entry on the ledger
type ProvenanceRecord struct {
	Hash           string `json:"hash"`
	MetadataJSON   string `json:"metadata_json"`
	Classification string `json:"classification"`
	Timestamp      string `json:"timestamp"`
	TxID           string `json:"tx_id"`
}

// ProvenanceContract provides chaincode functions for provenance tracking
type ProvenanceContract struct {
	contractapi.Contract
}

// StoreHash stores a new provenance hash on the ledger
func (c *ProvenanceContract) StoreHash(ctx contractapi.TransactionContextInterface, hash string, metadataJSON string, classification string) error {
	if hash == "" {
		return fmt.Errorf("hash cannot be empty")
	}

	existing, err := ctx.GetStub().GetState(hash)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if existing != nil {
		return fmt.Errorf("hash %s already exists", hash)
	}

	record := ProvenanceRecord{
		Hash:           hash,
		MetadataJSON:   metadataJSON,
		Classification: classification,
		Timestamp:      time.Now().UTC().Format(time.RFC3339),
		TxID:           ctx.GetStub().GetTxID(),
	}

	recordJSON, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}

	return ctx.GetStub().PutState(hash, recordJSON)
}

// VerifyHash checks if a hash exists and returns its metadata
func (c *ProvenanceContract) VerifyHash(ctx contractapi.TransactionContextInterface, hash string) (bool, string, string, error) {
	if hash == "" {
		return false, "", "", fmt.Errorf("hash cannot be empty")
	}

	recordJSON, err := ctx.GetStub().GetState(hash)
	if err != nil {
		return false, "", "", fmt.Errorf("failed to read state: %v", err)
	}
	if recordJSON == nil {
		return false, "", "", nil
	}

	var record ProvenanceRecord
	if err := json.Unmarshal(recordJSON, &record); err != nil {
		return false, "", "", fmt.Errorf("failed to unmarshal record: %v", err)
	}

	return true, record.Timestamp, record.TxID, nil
}

// GetHistory returns the full history for a hash (includes all updates, though StoreHash prevents duplicates)
func (c *ProvenanceContract) GetHistory(ctx contractapi.TransactionContextInterface, hash string) ([]ProvenanceRecord, error) {
	if hash == "" {
		return nil, fmt.Errorf("hash cannot be empty")
	}

	iterator, err := ctx.GetStub().GetHistoryForKey(hash)
	if err != nil {
		return nil, fmt.Errorf("failed to get history: %v", err)
	}
	defer iterator.Close()

	var records []ProvenanceRecord
	for iterator.HasNext() {
		response, err := iterator.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to iterate history: %v", err)
		}

		var record ProvenanceRecord
		if err := json.Unmarshal(response.Value, &record); err != nil {
			continue
		}
		records = append(records, record)
	}

	return records, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&ProvenanceContract{})
	if err != nil {
		fmt.Printf("Error creating provenance chaincode: %v", err)
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting provenance chaincode: %v", err)
	}
}
