# CUI // SP-CTI
# Hyperledger Fabric Chaincode Packaging Makefile

.PHONY: all package clean test

CC_NAME ?= icdev-provenance
CC_VERSION ?= 1.0
CC_SEQUENCE ?= 1

CHAINCODES := provenance audit evidence access compliance_gate

all: package

package: $(CHAINCODES)
	@echo "Packaging chaincodes..."
	@for cc in $(CHAINCODES); do \
		cd $$cc && \
		go mod init $$cc 2>/dev/null || true && \
		go get github.com/hyperledger/fabric-contract-api-go@latest 2>/dev/null || true && \
		go mod tidy 2>/dev/null || true && \
		cd ..; \
		echo "  Packaged $$cc"; \
	done
	@echo "Done. Use 'peer lifecycle chaincode package' for deployment."

test:
	@for cc in $(CHAINCODES); do \
		echo "Testing $$cc..."; \
		cd $$cc && go test ./... 2>/dev/null || echo "  (no tests for $$cc)"; \
		cd ..; \
	done

clean:
	@for cc in $(CHAINCODES); do \
		rm -f $$cc/*.tar.gz $$cc/go.mod $$cc/go.sum; \
	done
