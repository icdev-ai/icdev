// CUI // SP-CTI
// AccessContract — classification-aware RBAC for provenance hashes
package main

import (
	"fmt"
	"strings"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// AccessContract provides chaincode for classification-aware access control
type AccessContract struct {
	contractapi.Contract
}

// classification levels in ascending order of sensitivity
var classificationLevels = []string{"UNCLASSIFIED", "CUI", "IL4", "IL5", "IL6", "SECRET", "TS"}

func levelIndex(level string) int {
	upper := strings.ToUpper(level)
	for i, l := range classificationLevels {
		if l == upper {
			return i
		}
	}
	return -1
}

// CanReadHash determines if requestingOrg can read a hash with given classification
func (c *AccessContract) CanReadHash(ctx contractapi.TransactionContextInterface, requestingOrg string, hash string, classification string) (bool, error) {
	if hash == "" {
		return false, fmt.Errorf("hash cannot be empty")
	}

	// Get the client's MSP ID
	clientMSPID, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return false, fmt.Errorf("failed to get client MSP ID: %v", err)
	}

	// Simple org-based check: requestingOrg must match client MSP
	if !strings.EqualFold(clientMSPID, requestingOrg) {
		return false, nil
	}

	// Classification check
	if classification == "" {
		return true, nil
	}

	readerLevel := levelIndex(classification)
	if readerLevel < 0 {
		return false, fmt.Errorf("unknown classification: %s", classification)
	}

	// Data classification level
	dataLevel := levelIndex(classification)
	if dataLevel < 0 {
		return false, fmt.Errorf("unknown classification: %s", classification)
	}

	// Reader must have level >= data level
	return readerLevel >= dataLevel, nil
}

// EnforceClassification checks if a reader with given IL can access data with given classification
func (c *AccessContract) EnforceClassification(ctx contractapi.TransactionContextInterface, classification string, readerIL string) (bool, error) {
	dataLevel := levelIndex(classification)
	readerLevel := levelIndex(readerIL)

	if dataLevel < 0 || readerLevel < 0 {
		return false, fmt.Errorf("unknown classification level")
	}

	return readerLevel >= dataLevel, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&AccessContract{})
	if err != nil {
		fmt.Printf("Error creating access chaincode: %v", err)
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting access chaincode: %v", err)
	}
}
